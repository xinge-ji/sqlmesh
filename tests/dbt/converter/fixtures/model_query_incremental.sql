WITH cte AS (
    SELECT
        oi.order_id AS order_id,
    FROM {{ ref('order_items') }} AS oi
    LEFT JOIN {{ ref('items') }} AS i
    ON oi.item_id = i.id AND oi.ds = i.ds
{% if is_incremental() %}
WHERE
    oi.ds > (select max(ds) from {{ this }})
{% endif %}
{% if sqlmesh_incremental is defined %}
WHERE
    oi.ds BETWEEN '{{ start_ds }}' AND '{{ end_ds }}'
{% endif %}
GROUP BY
    oi.order_id,
    oi.ds
)
SELECT
    o.customer_id::INT AS customer_id, /* Customer id */
    SUM(ot.total)::NUMERIC AS revenue, /* Revenue from orders made by this customer */
    o.ds::TEXT AS ds /* Date */
FROM {{ ref('orders') }} AS o
    LEFT JOIN order_total AS ot
    ON o.id = ot.order_id AND o.ds = ot.ds
{% if is_incremental() %}
    WHERE o.ds > (select max(ds) from {{ this }})
{% endif %}
{% if sqlmesh_incremental is defined %}
    WHERE o.ds BETWEEN '{{ start_ds }}' AND '{{ end_ds }}'
{% endif %}
GROUP BY
    o.customer_id,
    o.ds