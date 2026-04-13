import sys
import os

# Add odoo to path
sys.path.append('.')

# This script is meant to be run via odoo-bin shell
# Usage: ./odoo-bin shell -d omv3 --no-http < scripts/sales_report.py

def get_report():
    from datetime import datetime, timedelta
    from odoo import fields
    
    date_30_days_ago = fields.Datetime.to_string(datetime.now() - timedelta(days=30))
    
    # Query for best sellers (Unified Sales + PoS)
    query = """
        SELECT pt.name, sum(qty) as total_qty, sum(price) as total_revenue
        FROM (
            SELECT l.product_id, l.product_uom_qty as qty, l.price_subtotal as price
            FROM sale_order_line l
            JOIN sale_order s ON l.order_id = s.id
            WHERE s.state IN ('sale', 'done') AND s.date_order >= %s
            UNION ALL
            SELECT l.product_id, l.qty as qty, l.price_subtotal as price
            FROM pos_order_line l
            JOIN pos_order p ON l.order_id = p.id
            WHERE p.state IN ('paid', 'done', 'invoiced') AND p.date_order >= %s
        ) as combined_sales
        JOIN product_product pp ON product_id = pp.id
        JOIN product_template pt ON pp.product_tmpl_id = pt.id
        GROUP BY pt.name
        ORDER BY total_revenue DESC
        LIMIT 10
    """
    
    env.cr.execute(query, (date_30_days_ago, date_30_days_ago))
    best_sellers = env.cr.fetchall()
    
    print("\n" + "="*70)
    print("      UNIFIED SALES & POS REPORT (LAST 30 DAYS)")
    print("="*70)
    if not best_sellers:
        print("No sales recorded in the last 30 days.")
    else:
        print(f"{'Product Name':<40} | {'Qty Sold':<10} | {'Revenue':<10}")
        print("-" * 70)
        for name, qty, rev in best_sellers:
            print(f"{name:<40} | {qty:<10.2f} | {rev:<10.2f}")
    print("="*70 + "\n")

if __name__ == "__main__":
    get_report()
