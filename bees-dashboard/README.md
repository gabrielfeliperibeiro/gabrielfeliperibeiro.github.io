# BEES Live Orders Dashboard

A stunning real-time orders tracking dashboard built with the BEES brand design system. This dashboard connects to Databricks to display live order data from the Philippines market.

## Features

- **Real-time Data**: Auto-refreshes every 30 seconds
- **Beautiful BEES Design**: Gold/yellow themed following BEES brand guidelines
- **Animated Statistics**: Smooth number animations and visual transitions
- **Order Tracking**: Live order table with status indicators
- **Channel Analytics**: Performance breakdown by channel
- **Activity Feed**: Real-time activity stream
- **Hourly Timeline**: Visual chart of order distribution
- **Demo Mode**: Works without API connection for demonstrations
- **Secure Architecture**: API token never exposed in frontend

## Architecture

```
┌─────────────────┐     ┌──────────────────────┐     ┌─────────────────┐
│  GitHub Pages   │────▶│  Cloudflare Worker   │────▶│   Databricks    │
│   (Frontend)    │     │     (API Proxy)      │     │   SQL Endpoint  │
└─────────────────┘     └──────────────────────┘     └─────────────────┘
```

The dashboard uses a secure proxy architecture:
1. **Frontend** (GitHub Pages): Static HTML/CSS/JS dashboard
2. **API Proxy** (Cloudflare Worker): Securely stores Databricks token
3. **Backend** (Databricks): SQL warehouse for order data

## Quick Start

### Option 1: Demo Mode
Simply open the dashboard and click "Use Demo Mode" to see simulated data.

### Option 2: Connect to Databricks

#### Step 1: Deploy the Cloudflare Worker

1. Create a free [Cloudflare account](https://cloudflare.com)
2. Go to **Workers & Pages** → **Create Worker**
3. Copy the contents of `cloudflare-worker.js` into the worker
4. Go to **Settings** → **Variables** → **Add Secret**:
   - Name: `DATABRICKS_TOKEN`
   - Value: `<your-databricks-personal-access-token>`
5. Add another variable:
   - Name: `ALLOWED_ORIGINS`
   - Value: `https://gabrielfeliperibeiro.github.io`
6. Click **Deploy**
7. Copy your worker URL (e.g., `https://your-worker-name.your-subdomain.workers.dev`)

#### Step 2: Configure the Dashboard

1. Open the dashboard
2. Enter your Cloudflare Worker URL when prompted
3. Click **Connect**

The dashboard will now fetch real data from Databricks!

## Databricks Configuration

The worker is configured with:

| Setting | Value |
|---------|-------|
| Server Hostname | `adb-1825183661408911.11.azuredatabricks.net` |
| Port | `443` |
| HTTP Path | `/sql/protocolv1/o/1825183661408911/0523-172047-4vu5f6v7` |
| Warehouse ID | `0523-172047-4vu5f6v7` |

### SQL Query

```sql
SELECT
  country,
  placement_date,
  vendor_id,
  order_number,
  order_status,
  order_gmv,
  order_gmv_usd,
  account_id,
  vendor_account_id,
  channel,
  source,
  minute_aging,
  is_last_minute,
  is_last_30_minutes,
  is_last_hour,
  is_today
FROM wh_am.sandbox.orders_live_tracking
WHERE country = 'PH'
AND TO_DATE(DATE_TRUNC('DAY', placement_date)) = CURRENT_DATE()
ORDER BY placement_date DESC
```

## API Endpoints

The Cloudflare Worker exposes these endpoints:

| Endpoint | Description |
|----------|-------------|
| `GET /api/orders` | Fetch all orders for today |
| `GET /api/summary` | Fetch aggregated statistics |
| `GET /api/health` | Health check endpoint |

## Security Best Practices

This dashboard follows security best practices:

1. **No Exposed Credentials**: The Databricks token is stored as a Cloudflare Worker secret, never in frontend code
2. **CORS Protection**: Only allowed origins can access the API
3. **HTTPS Only**: All communication is encrypted
4. **No Local Storage of Tokens**: Only the Worker URL is stored in browser

## Customization

### Changing the Refresh Interval

Edit the `CONFIG` object in `index.html`:

```javascript
const CONFIG = {
    refreshInterval: 30000, // Change to desired milliseconds
};
```

### Modifying the Query

Edit the `ORDERS_QUERY` constant in `cloudflare-worker.js` to change:
- Filters (country, date range)
- Columns selected
- Sort order

### Styling

The dashboard uses CSS variables for easy theming:

```css
:root {
    --bees-gold: #FFD200;
    --bees-black: #1A1A1A;
    /* ... more variables */
}
```

## File Structure

```
bees-dashboard/
├── index.html           # Main dashboard (HTML/CSS/JS)
├── cloudflare-worker.js # Secure API proxy for Databricks
└── README.md           # This file
```

## Troubleshooting

### "Failed to fetch data" Error
- Check that your Cloudflare Worker is deployed and running
- Verify the Worker URL in the dashboard configuration
- Check the Worker logs in Cloudflare dashboard

### Data Not Updating
- Ensure the Databricks warehouse is running
- Check that the token hasn't expired
- Verify the SQL query returns data

### CORS Errors
- Add your domain to `ALLOWED_ORIGINS` in Worker settings
- Include both `http://` and `https://` versions if needed

## Support

For issues with:
- **Dashboard UI**: Check browser console for JavaScript errors
- **API Proxy**: Check Cloudflare Worker logs
- **Databricks Query**: Test query directly in Databricks workspace
