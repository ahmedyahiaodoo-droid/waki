def post_init_hook(env):
    """Create a Waki warehouse for every Odoo warehouse and mirror all open MOs."""
    WakiWarehouse = env['waki.warehouse'].sudo()
    for warehouse in env['stock.warehouse'].sudo().with_context(active_test=False).search([]):
        WakiWarehouse._get_for_warehouse(warehouse)
    open_mos = env['mrp.production'].sudo().search([('state', 'not in', ('done', 'cancel'))])
    open_mos._waki_sync_production()
