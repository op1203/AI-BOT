import sys
import os

# Add odoo to path
sys.path.append('.')

# This script is meant to be run via odoo-bin shell
# Usage: ./odoo-bin shell -d omv3 --no-http < get_report.py

def get_report():
    from datetime import datetime, timedelta
    from odoo import fields
    
    date_30_days_ago = fields.Datetime.to_string(datetime.now() - timedelta(days=30))
    
    # Query for best sellers
    env.cr.execute("""
        SELECT pt.name, sum(l.product_uom_qty) as total_qty, sum(l.price_subtotal) as total_revenue
        FROM sale_order_line l
        JOIN product_product pp ON l.product_id = pp.id
        JOIN product_template pt ON pp.product_tmpl_id = pt.id
        WHERE l.state IN ('sale', 'done') AND l.create_date >= %s
        GROUP BY pt.name
        ORDER BY total_qty DESC
        LIMIT 10
    """, (date_30_days_ago,))
    
    best_sellers = env.cr.fetchall()
    
    print("\n--- SALES REPORT (LAST 30 DAYS) ---")
    if not best_sellers:
        print("No sales recorded in the last 30 days.")
    else:
        print(f"{'Product Name':<40} | {'Qty Sold':<10} | {'Revenue':<10}")
        print("-" * 66)
        for name, qty, rev in best_sellers:
            print(f"{name:<40} | {qty:<10.2f} | {rev:<10.2f}")
    print("----------------------------------\n")

get_report()
