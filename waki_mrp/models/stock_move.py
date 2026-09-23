from odoo import models
from odoo.tools import float_is_zero


class StockMove(models.Model):
    _inherit = 'stock.move'

    def _action_done(self, cancel_backorder=False):
        moves = super()._action_done(cancel_backorder=cancel_backorder)
        moves._waki_sync_receipts()
        return moves

    def _waki_sync_receipts(self):
        """Mirror vendor receipts / returns (quantity only) into waki warehouses.
        MO raw/finished moves are ignored: the waki MO handles them by standard BoM."""
        WakiMove = self.env['waki.move'].sudo()
        WakiWarehouse = self.env['waki.warehouse'].sudo()
        todo = self.filtered(lambda m: m.state == 'done'
                             and not m.raw_material_production_id and not m.production_id)
        if not todo:
            return
        already = WakiMove.search([('stock_move_id', 'in', todo.ids)]).stock_move_id
        vals_list = []
        for move in todo - already:
            src, dest = move.location_id, move.location_dest_id
            if src.usage == 'supplier' and dest.usage == 'internal':
                direction, move_type, warehouse = 'in', 'purchase_receipt', dest.warehouse_id
            elif src.usage == 'internal' and dest.usage == 'supplier':
                direction, move_type, warehouse = 'out', 'purchase_return', src.warehouse_id
            else:
                continue
            waki_wh = WakiWarehouse._get_for_warehouse(warehouse)
            if not waki_wh or not waki_wh.sync_receipts:
                continue
            qty = move.product_uom._compute_quantity(move.quantity, move.product_id.uom_id)
            if float_is_zero(qty, precision_rounding=move.product_id.uom_id.rounding):
                continue
            vals_list.append({
                'waki_warehouse_id': waki_wh.id,
                'product_id': move.product_id.id,
                'quantity': qty,
                'direction': direction,
                'move_type': move_type,
                'origin': move.picking_id.name or move.reference or move.origin,
                'stock_move_id': move.id,
            })
        if vals_list:
            WakiMove.create(vals_list).action_done()
