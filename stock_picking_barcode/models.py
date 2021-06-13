# -*- coding: utf-8 -*-

from odoo import models, api
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_compare


class StockPicking(models.Model):
    _inherit = "stock.picking"

    @api.multi
    def book_picking(self):
        # TODO: book picking. put zero qty on backorder and cancel backorder
        return {
            "success": True,
            "msg": "Test",
            "picking_id": self.id,
        }

    @api.multi
    def process_barcode_from_ui(self, barcode_str, visible_op_ids):
        """This function is called each time there barcode scanner reads an input"""
        self.ensure_one()
        lot_obj = self.env['stock.production.lot']
        package_obj = self.env['stock.quant.package']
        product_obj = self.env['product.product']
        pack_op = self.env['stock.pack.operation'].search(
            [('picking_id', '=', self.id)])
        stock_location_obj = self.env['stock.location']
        answer = {'filter_loc': False, 'operation_id': False}
        # check if the barcode correspond to a location
        matching_location_ids = stock_location_obj.search([('barcode', '=', barcode_str)])
        if matching_location_ids:
            # if we have a location, return immediatly with the location name
            location = stock_location_obj.browse(matching_location_ids[0])
            answer['filter_loc'] = stock_location_obj._name_get(location)
            answer['filter_loc_id'] = matching_location_ids[0]
            return answer
        # check if the barcode correspond to a product
        matching_product_ids = product_obj.search(['|', ('barcode', '=', barcode_str),
                                                            ('default_code', '=', barcode_str)])
        if matching_product_ids:
            op_id = pack_op._increment(
                self.id,
                [('product_id', '=', matching_product_ids[0].id)],
                filter_visible=True,
                visible_op_ids=visible_op_ids,
                increment=True
            )
            answer['operation_id'] = op_id.id
            return answer
        # check if the barcode correspond to a lot
        matching_lot_ids = lot_obj.search([('name', '=', barcode_str)])
        if matching_lot_ids:
            lot = lot_obj.browse(matching_lot_ids[0].id)
            op_id = self.env['stock.pack.operation']
            linked_pack_lots = self.env['stock.pack.operation.lot']
            for po in self.pack_operation_ids:
                for pol in po.pack_lot_ids:
                    linked_pack_lots += pol
                    if pol.lot_id.id == lot.id and pol.qty == 0 and pol.operation_id:
                        op_id = pack_op._increment(
                            self.id,
                            [('product_id', '=', lot.product_id.id), ('pack_lot_ids.lot_id', '=', lot.id)],
                            filter_visible=True,
                            visible_op_ids=visible_op_ids,
                            increment=True
                        )
                    # lot is already on picking and qty is 1. so nothing to do
                    elif pol.lot_id.id == lot.id and pol.qty == 1 and pol.operation_id:
                        return pol.operation_id.id
            if not op_id and self.should_replace():  # lot not on picking. replace with scanned one
                # Actually Odoo allow multiple occurrences of one lot number in different stock locations.
                # That's why we use a precise search first.
                quant = self.env['stock.quant'].search([('lot_id', '=', lot.id),
                                                        ('location_id', '=', self.location_id.id)])
                if len(quant) > 1:
                    msg = 'Multiple Quants found for S/N in stock location: S/N %s, Quants %s, Location %s'
                    vals = (lot.name, str(quant), self.location_id.name)
                    raise UserError(msg % vals)
                if len(quant) == 0:
                    # Try softer search to give a hint to the user.
                    quant = self.env['stock.quant'].search([('lot_id', '=', lot.id)])
                    if len(quant) == 0:
                        raise UserError('No Quant found for S/N: %s' % lot.name)
                    if len(quant) > 1:
                        msg = 'Multiple Quants found for S/N in other stock location(s): S/N %s, Quants %s'
                        vals = (lot.name, str(quant))
                        raise UserError(msg % vals)
                    if quant.location_id != self.location_id:

                        self.fix_wrong_location(quant)
                        if not self.should_replace():  # instead of replace we will add a new line
                            op_id = pack_op._increment(
                                self.id,
                                [("product_id", "=", lot.product_id.id),
                                 ("pack_lot_ids.lot_id", "=", lot.id)],
                                filter_visible=True,
                                visible_op_ids=visible_op_ids,
                                increment=True,
                            )
                            answer["operation_id"] = op_id.id
                            return answer
                pack_lot = self.env['stock.pack.operation.lot'].search([
                    ('lot_id', '=', lot.id),
                    # ('qty', '=', 0),
                    ('operation_id', '!=', False),
                    # we are not interested in done pickings
                    ('operation_id.picking_id.state', '=', 'assigned'),
                ])
                available_pack_ops = self.env['stock.pack.operation'].search([
                    ('picking_id', '=', self[0].id),
                    ('product_id', '=', lot.product_id.id),
                ])
                if not pack_lot:
                    # there is no stock.pack.operation.lot for this lot.
                    # search for a stock.pack.operation.lot to replace.
                    replaced = False
                    for pack_op in available_pack_ops:
                        for pack_lot in pack_op.pack_lot_ids:
                            if pack_lot.qty == 0 and not op_id:
                                pack_lot.unlink()
                                pack_lot = self.env['stock.pack.operation.lot'].create({
                                    'lot_id': lot.id,
                                    'operation_id': pack_op.id,
                                    'qty_todo': 1,
                                    'qty': 0,
                                })
                                op_id = pack_op
                                pack_op.qty_done += 1
                                pack_lot.qty += 1
                                replaced = True
                    if not replaced:
                        # Its a lot product so qty == 1
                        op_id = self.add_to_picking(barcode_str, 1, self)
                        op_id.qty_done += 1
                else:
                    for pack_op in available_pack_ops:
                        for pack_lot_self in pack_op.pack_lot_ids:
                            if pack_lot_self.qty == 0 and pack_lot.qty == 0 and not op_id:
                                op = pack_lot.operation_id
                                pack_lot.operation_id = pack_op.id
                                pack_lot_self.operation_id = op.id
                                op_id = pack_op
                                pack_op.qty_done += 1
                                pack_lot.qty += 1  # this one is our new operation lot
            # In case Product is on picking but lot not and we must not replace
            elif not op_id and not self.should_replace():
                op_id = self.add_to_picking(barcode_str, 1, self)
                op_id.qty_done += 1
                # picking sets itself to draft. so we need to confirm it again
                self.action_confirm()
            answer['operation_id'] = op_id.id
            return answer
        # check if the barcode correspond to a package
        matching_package_ids = package_obj.search([('name', '=', barcode_str)])
        if matching_package_ids:
            op_id = pack_op._increment(
                self.id,
                [('package_id', '=', matching_package_ids[0])],
                filter_visible=True,
                visible_op_ids=visible_op_ids,
                increment=True
            )
            answer['operation_id'] = op_id.id
            return answer
        return answer

    @api.model
    def add_to_picking(self, barcode, qty, picking):
        self.ensure_one()
        company = self.env.ref("base.main_company")

        # barcode belongs to product
        res = self.get_product_by_barcode(barcode)
        if res:
            self.env["stock.move"].create({
                "product_uom": res.uom_id.id,
                "company_id": company.id,
                "location_id": self.location_id.id,
                "location_dest_id": self.location_dest_id.id,
                "product_id": res.id,
                "name": res.name,
                "picking_id": picking.id,
                "product_uom_qty": qty,
            })
            return True

        # barcode belongs to lot
        res = self.get_lot_by_barcode(barcode)
        if res:
            quant = res.quant_ids
            if qty != 1:
                raise UserError(
                    u"Für Seriennummern ist nur die Menge 1 zulässig. Bitte "
                    u"beachten Sie, dass Zeilen mit gleichem Barcode für die "
                    u"Verarbeitung zusammengezogen werden. "
                    u"Angegebene Menge: %s, Barcode: %s" % (qty, barcode))
            if len(quant) != 1:
                raise UserError(
                    u"Der Bestand für die Seriennummer ist fehlerhaft. Zu "
                    u"jeder Seriennummer muss es genau ein Produkt geben. "
                    u"Seriennummer: %s, Menge: %s" % (barcode, len(quant)))
            if quant.location_id != self.location_id:
                picking.fix_wrong_location(quant, create_pack_ops=True,
                                           from_csv=True)

            # one move per lot? Should also be possible to raise the qty of
            # the move and handle additional lot with pack operations
            move = self.env["stock.move"].create({
                "product_uom": quant.product_id.uom_id.id,
                "company_id": company.id,
                # quant should be at right location from fix location function
                "location_id": self.location_id.id,
                "location_dest_id": self.location_dest_id.id,
                "product_id": quant.product_id.id,
                "name": quant.product_id.name,
                "picking_id": picking.id,
                "product_uom_qty": qty,
            })
            pack_op = self.env["stock.pack.operation"].create({
                "location_id": move.location_id.id,
                "location_dest_id": move.location_dest_id.id,
                "picking_id": picking.id,
                "product_id": quant.product_id.id,
                "product_qty": qty,
                "product_uom_id": move.product_uom.id,
            })
            self.env["stock.pack.operation.lot"].create({
                "lot_id": quant.lot_id.id,
                "qty_todo": qty,
                "operation_id": pack_op.id
            })
            self.env["stock.move.operation.link"].create({
                "move_id": move.id,
                "operation_id": pack_op.id,
                "qty": qty
            })

            return pack_op

        # barcode doesn't belong to Odoo. we will collect unknown barcodes.
        # so just return failure
        return False

    @api.model
    def get_product_by_barcode(self, barcode):
        res = self.env["product.product"].search([("barcode", "=", barcode)])
        if len(res) > 1:
            raise UserError(u"Zu dem Barcode gibt es mehrere Produkte. Dies "
                            u"ist nicht zulässig! barcode: %s" % barcode)
        return res

    @api.model
    def get_lot_by_barcode(self, barcode):
        res = self.env["stock.production.lot"].search([
            ("name", "=", barcode)])
        if len(res) > 1:
            raise UserError(u"Zu dem Barcode gibt es mehrere Seriennummern. "
                            u"Dies ist nicht zulässig! barcode: %s" % barcode)
        return res

    @api.multi
    def should_replace(self):
        return False

    @api.multi
    def fix_wrong_location(self, quant):
        raise UserError(
            'The scanned product is stored in an unexpected '
            'location. Expected: %s but is %s' %
            (self.location_id.name, quant.location_id.name))

    def get_next_picking_for_ui(self):
        """ returns the next pickings to process. Used in the barcode scanner UI"""
        domain = [('state', 'in', ('assigned', 'partially_available'))]
        if self.env.context.get('default_picking_type_id'):
            domain.append(('picking_type_id', '=', self.env.context['default_picking_type_id']))
        return self.search(domain).ids

    @api.model
    def check_group_lot(self):
        """ This function will return true if we have the setting to use lots activated. """
        return self.env['res.users'].has_group('stock.group_production_lot')

    @api.model
    def check_group_pack(self):
        """ This function will return true if we have the setting to use package activated. """
        return self.env['res.users'].has_group('stock.group_tracking_lot')

    def action_assign_owner(self):
        for picking in self:
            packop_ids = [op.id for op in picking.pack_operation_ids]
            self.env['stock.pack.operation'].write(packop_ids, {'owner_id': picking.owner_id.id})

    @api.multi
    def process_product_id_from_ui(self, product_id, op_id, increment=True):
        self.ensure_one()
        pack_op = self.env['stock.pack.operation'].search(
            [('picking_id', '=', self.id)])
        op_obj = pack_op._increment(
            self.id,
            [('product_id', '=', product_id), ('id', '=', op_id)],
            increment=increment
        )
        return op_obj.id

    @api.cr_uid_ids_context
    def action_pack(self, picking_ids, operation_filter_ids=None):
        """ Create a package with the current pack_operation_ids of the picking that aren't yet in a pack.
        Used in the barcode scanner UI and the normal interface as well.
        operation_filter_ids is used by barcode scanner interface to specify a subset of operation to pack"""
        if operation_filter_ids is None:
            operation_filter_ids = []
        stock_operation_obj = self.env['stock.pack.operation']
        package_obj = self.env['stock.quant.package']
        stock_move_obj = self.env['stock.move']
        package_id = False
        for picking_id in picking_ids:
            operation_search_domain = [('picking_id', '=', picking_id), ('result_package_id', '=', False)]
            if operation_filter_ids != []:
                operation_search_domain.append(('id', 'in', operation_filter_ids))
            operation_ids = stock_operation_obj.search(operation_search_domain)
            pack_operation_ids = []
            if operation_ids:
                for operation in stock_operation_obj.browse(operation_ids):
                    # If we haven't done all qty in operation, we have to split into 2 operation
                    op = operation
                    if (operation.qty_done < operation.product_qty):
                        new_operation = operation.copy(
                            {'product_qty': operation.qty_done, 'qty_done': operation.qty_done},
                        )
                        operation.write(
                            {'product_qty': operation.product_qty - operation.qty_done, 'qty_done': 0},
                        )
                        op = stock_operation_obj.browse(new_operation)
                    pack_operation_ids.append(op.id)
                    if op.product_id and op.location_id and op.location_dest_id:
                        stock_move_obj.check_tracking_product(
                            op.product_id,
                            op.lot_id.id,
                            op.location_id,
                            op.location_dest_id
                        )
                package_id = package_obj.create({})
                stock_operation_obj.browse(pack_operation_ids).write(
                    {'result_package_id': package_id},
                )
        return package_id

    @api.multi
    def action_done_from_ui(self, picking_id):
        """ called when button 'done' is pushed in the barcode scanner UI """
        # write qty_done into field product_qty for every package_operation before doing the transfer
        # for operation in self.pack_operation_ids:
        #     operation.with_context(no_recompute=True).write({'product_qty': operation.qty_done})
        self.do_new_transfer()
        # return id of next picking to work on
        return self.get_next_picking_for_ui()

    def unpack(self):
        quant_obj = self.env['stock.quant']
        for package in self:
            quant_ids = [quant.id for quant in package.quant_ids]
            quant_obj.write(quant_ids, {'package_id': package.parent_id.id or False})
            children_package_ids = [child_package.id for child_package in package.children_ids]
            self.write(children_package_ids, {'parent_id': package.parent_id.id or False})
        # delete current package since it contains nothing anymore
        self.unlink()
        return self.env['ir.actions.act_window'].for_xml_id(
            'stock',
            'action_package_view',
        )

    @api.multi
    def open_barcode_interface(self):
        picking_ids = self.ids
        final_url = "/barcode/web/#action=stock.ui&picking_id=" + str(picking_ids[0])
        return {'type': 'ir.actions.act_url', 'url': final_url, 'target': 'self', }

    @api.cr_uid_ids_context
    def do_partial_open_barcode(self, picking_ids):
        self.do_prepare_partial(picking_ids)
        return self.open_barcode_interface(picking_ids)


class StockPickingType(models.Model):
    _inherit = "stock.picking.type"

    def open_barcode_interface(self):
        final_url = "/barcode/web/#action=stock.ui&picking_type_id=" + str(self.ids[0]) if len(self.ids) else '0'
        return {'type': 'ir.actions.act_url', 'url': final_url, 'target': 'self'}


class StockPackOperation(models.Model):
    _inherit = "stock.pack.operation"

    @api.multi
    def _increment(self, picking_id, domain, filter_visible=False, visible_op_ids=False, increment=True):
        """Search for an operation with given 'domain' in a picking, if it exists increment the qty (+1) otherwise create it

        :param domain: list of tuple directly reusable as a domain
        context can receive a key 'current_package_id' with the package to consider for this operation
        returns True
        """
        operation = self[0]  # TODO: Ignore multiple records for now. We should iterate later
        # if current_package_id is given in the context, we increase the number of items in this package
        package_clause = [('result_package_id', '=', operation.env.context.get('current_package_id', False))]
        existing_operation_ids = operation.search([('picking_id', '=', picking_id)] + domain + package_clause)
        todo_operation_ids = []
        if existing_operation_ids:
            if filter_visible:
                todo_operation_ids = [val for val in existing_operation_ids if val.id in visible_op_ids]
            else:
                todo_operation_ids = existing_operation_ids
        if todo_operation_ids:
            # existing operation found for the given domain and picking => increment its quantity
            op_obj = todo_operation_ids[0]
            # when op_object has a lot
            if op_obj.pack_lot_ids:
                for pack_lot in op_obj.pack_lot_ids:
                    if pack_lot.lot_id.id == domain[1][2] and pack_lot.lot_id.product_id.id == domain[0][2] and pack_lot.qty == 0:
                        qty = op_obj.qty_done
                        qty_pack_lot = pack_lot.qty
                        if increment:
                            qty += 1
                            qty_pack_lot += 1
                        else:
                            qty -= 1 if qty >= 1 else 0
                            qty_pack_lot -= 1 if qty >= 1 else 0
                            # TODO: Removed removel for first
                            # if qty == 0 and op_obj.product_qty == 0:
                            #     # we have a line with 0 qty set, so delete it
                            #     operation.unlink([op_obj.id])
                            #     return False
                        op_obj.write({'qty_done': qty})
                        pack_lot.write({'qty': qty_pack_lot})
                        return op_obj
            # operation with no-lot-product
            elif op_obj.product_id.id == domain[0][2]:
                qty = op_obj.qty_done
                if increment:
                    qty += 1
                else:
                    qty -= 1 if qty >= 1 else 0
                    # TODO: Removed removel for first
                    # if qty == 0 and op_obj.product_qty == 0:
                    #     # we have a line with 0 qty set, so delete it
                    #     operation.unlink([op_obj.id])
                    #     return False
                op_obj.write({'qty_done': qty})
                return op_obj
        else:
            # no existing operation found for the given domain and picking => create a new one
            picking_obj = operation.env["stock.picking"]
            picking = picking_obj.browse(picking_id)
            values = {
                'picking_id': picking_id,
                'product_qty': 0,
                'location_id': picking.location_id.id,
                'location_dest_id': picking.location_dest_id.id,
                'qty_done': 1,
            }
            for key in domain:
                var_name, dummy, value = key
                uom_id = False
                if var_name == 'product_id':
                    uom_id = operation.env['product.product'].browse(value).uom_id.id
                if var_name == 'pack_lot_ids.lot_id':
                    update_dict = {'pack_lot_ids': [(0, 0, {'lot_id': value})]}
                else:
                    update_dict = {var_name: value}
                if uom_id:
                    update_dict['product_uom_id'] = uom_id
                values.update(update_dict)
            return operation.create(values)
        return self.env['stock.pack.operation']

    @api.multi
    def create_and_assign_lot(self, name):
        """ Used by barcode interface to create a new lot and assign it to the operation """
        self.ensure_one()
        product_id = self.product_id.id
        val = {'product_id': product_id}
        new_lot_id = False
        if name:
            lots = self.env['stock.production.lot'].search(
                ['&', ('name', '=', name), ('product_id', '=', product_id)],
            )
            if lots:
                new_lot_id = lots.ids[0]
            val.update({'name': name})

        if not new_lot_id:
            new_lot_id = self.env['stock.production.lot'].create(val)
        self.write({'pack_lot_ids': [(0, 0, {'lot_id': new_lot_id})]})
