from odoo import api, models


class StockWarehouse(models.Model):
    _inherit = 'stock.warehouse'

    @api.model_create_multi
    def create(self, vals_list):
        warehouses = super().create(vals_list)
        WakiWarehouse = self.env['waki.warehouse'].sudo()
        for warehouse in warehouses:
            WakiWarehouse._get_for_warehouse(warehouse)
        return warehouses
