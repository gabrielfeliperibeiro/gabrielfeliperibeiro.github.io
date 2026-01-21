/**
 * BEES Dashboard - Cloudflare Worker Proxy for Databricks SQL API
 *
 * This worker securely proxies requests to Databricks SQL API.
 * The Databricks token is stored as a Cloudflare Worker Secret (never exposed to clients).
 *
 * SETUP INSTRUCTIONS:
 * 1. Create a Cloudflare account at https://cloudflare.com
 * 2. Go to Workers & Pages > Create Worker
 * 3. Copy this code into the worker
 * 4. Add secrets in Settings > Variables:
 *    - DATABRICKS_TOKEN: Your Databricks personal access token
 *    - ALLOWED_ORIGINS: Comma-separated list of allowed origins (e.g., "https://yourusername.github.io")
 * 5. Deploy the worker
 * 6. Update the dashboard config with your worker URL
 */

// Databricks configuration
const DATABRICKS_HOST = 'adb-1825183661408911.11.azuredatabricks.net';
const DATABRICKS_HTTP_PATH = '/sql/protocolv1/o/1825183661408911/0523-172047-4vu5f6v7';
const WAREHOUSE_ID = '0523-172047-4vu5f6v7';

// SQL Query for orders tracking
const ORDERS_QUERY = `
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
`;

export default {
  async fetch(request, env, ctx) {
    // Handle CORS preflight
    if (request.method === 'OPTIONS') {
      return handleCORS(request, env);
    }

    // Validate origin
    const origin = request.headers.get('Origin');
    const allowedOrigins = (env.ALLOWED_ORIGINS || '').split(',').map(o => o.trim());

    // Allow localhost for development
    const isAllowed = allowedOrigins.includes(origin) ||
                      origin?.includes('localhost') ||
                      origin?.includes('127.0.0.1') ||
                      origin?.includes('.github.io');

    if (!isAllowed && origin) {
      return new Response('Forbidden', { status: 403 });
    }

    try {
      const url = new URL(request.url);
      const path = url.pathname;

      // Route handling
      if (path === '/api/orders' || path === '/') {
        return await fetchOrders(env, origin);
      } else if (path === '/api/health') {
        return jsonResponse({ status: 'ok', timestamp: new Date().toISOString() }, origin);
      } else if (path === '/api/summary') {
        return await fetchOrdersSummary(env, origin);
      }

      return new Response('Not Found', { status: 404 });
    } catch (error) {
      console.error('Worker error:', error);
      return jsonResponse({ error: error.message }, origin, 500);
    }
  }
};

async function fetchOrders(env, origin) {
  const token = env.DATABRICKS_TOKEN;

  if (!token) {
    return jsonResponse({ error: 'Databricks token not configured' }, origin, 500);
  }

  // Databricks SQL Statement Execution API
  const statementEndpoint = `https://${DATABRICKS_HOST}/api/2.0/sql/statements`;

  const response = await fetch(statementEndpoint, {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      warehouse_id: WAREHOUSE_ID,
      statement: ORDERS_QUERY,
      wait_timeout: '30s',
      on_wait_timeout: 'CANCEL',
      format: 'JSON_ARRAY'
    })
  });

  if (!response.ok) {
    const errorText = await response.text();
    console.error('Databricks API error:', errorText);
    return jsonResponse({ error: 'Failed to fetch data from Databricks', details: errorText }, origin, response.status);
  }

  const data = await response.json();

  // Transform data to a cleaner format
  const orders = transformDatabricksResponse(data);

  return jsonResponse({
    success: true,
    timestamp: new Date().toISOString(),
    count: orders.length,
    data: orders
  }, origin);
}

async function fetchOrdersSummary(env, origin) {
  const token = env.DATABRICKS_TOKEN;

  if (!token) {
    return jsonResponse({ error: 'Databricks token not configured' }, origin, 500);
  }

  const summaryQuery = `
    SELECT
      COUNT(*) as total_orders,
      COUNT(DISTINCT vendor_id) as unique_vendors,
      SUM(order_gmv_usd) as total_gmv_usd,
      AVG(order_gmv_usd) as avg_order_value_usd,
      SUM(CASE WHEN order_status = 'PLACED' THEN 1 ELSE 0 END) as placed_count,
      SUM(CASE WHEN order_status = 'INVOICED' THEN 1 ELSE 0 END) as invoiced_count,
      SUM(CASE WHEN order_status = 'DELIVERED' THEN 1 ELSE 0 END) as delivered_count,
      SUM(CASE WHEN order_status = 'CANCELLED' THEN 1 ELSE 0 END) as cancelled_count,
      SUM(CASE WHEN is_last_minute = 1 THEN 1 ELSE 0 END) as last_minute_orders,
      SUM(CASE WHEN is_last_30_minutes = 1 THEN 1 ELSE 0 END) as last_30min_orders,
      SUM(CASE WHEN is_last_hour = 1 THEN 1 ELSE 0 END) as last_hour_orders,
      MIN(placement_date) as first_order_time,
      MAX(placement_date) as last_order_time
    FROM wh_am.sandbox.orders_live_tracking
    WHERE country = 'PH'
    AND TO_DATE(DATE_TRUNC('DAY', placement_date)) = CURRENT_DATE()
  `;

  const statementEndpoint = `https://${DATABRICKS_HOST}/api/2.0/sql/statements`;

  const response = await fetch(statementEndpoint, {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      warehouse_id: WAREHOUSE_ID,
      statement: summaryQuery,
      wait_timeout: '30s',
      on_wait_timeout: 'CANCEL',
      format: 'JSON_ARRAY'
    })
  });

  if (!response.ok) {
    const errorText = await response.text();
    return jsonResponse({ error: 'Failed to fetch summary', details: errorText }, origin, response.status);
  }

  const data = await response.json();
  const summary = transformDatabricksResponse(data);

  return jsonResponse({
    success: true,
    timestamp: new Date().toISOString(),
    data: summary[0] || {}
  }, origin);
}

function transformDatabricksResponse(data) {
  if (data.status?.state !== 'SUCCEEDED') {
    throw new Error(`Query failed with state: ${data.status?.state}`);
  }

  const columns = data.manifest?.schema?.columns || [];
  const rows = data.result?.data_array || [];

  return rows.map(row => {
    const obj = {};
    columns.forEach((col, index) => {
      obj[col.name] = row[index];
    });
    return obj;
  });
}

function handleCORS(request, env) {
  const origin = request.headers.get('Origin');
  return new Response(null, {
    status: 204,
    headers: {
      'Access-Control-Allow-Origin': origin || '*',
      'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type, Authorization',
      'Access-Control-Max-Age': '86400'
    }
  });
}

function jsonResponse(data, origin, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      'Content-Type': 'application/json',
      'Access-Control-Allow-Origin': origin || '*',
      'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
      'Cache-Control': 'no-cache, no-store, must-revalidate'
    }
  });
}
