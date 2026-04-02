{
    "name": "Inventory AI Bot",
    "version": "1.0.0",
    "category": "Tools",
    "summary": "AI Bot for inventory and operations",
    "description": "Custom AI bot module for Odoo Community 18",
    "author": "Your Name",
    "depends": ["base", "stock", "mail", "sale"],
    "data": [
        "security/ir.model.access.csv",
        "data/discuss_channel_data.xml",
        "data/ir_cron_data.xml",
        "views/inventory_ai_views.xml",
        "views/res_config_settings_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "inventory_ai/static/src/css/ai_style.css",
            "inventory_ai/static/src/js/ai_typing.js",
            "inventory_ai/static/src/components/chat_widget/chat_widget.scss",
            "inventory_ai/static/src/components/chat_widget/chat_widget.js",
            "inventory_ai/static/src/components/chat_widget/chat_widget.xml",
        ],
    },
    "application": True,
    "installable": True,
    "license": "LGPL-3",
}