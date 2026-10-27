
-- 0. 
SELECT id,
    status,
       external_reference,
       customer_name,
       customer_email,
       product_code,
       status,
       created_at
FROM endpoint0.sales
LIMIT 1000;

-- 1. 
SELECT id,
        status,
       sale_id,
       file_name,
       content,
       created_at
FROM endpoint1.documents
LIMIT 1000;

-- 2. 
SELECT id,
        status,
       sale_id,
       policy_number,
       created_at
FROM endpoint2.policies
LIMIT 1000;


-- 3. 
SELECT id,
status,
       sale_id,
       document_id,
       bucket,
       object_key,
       uploaded_at
FROM endpoint3.uploads
LIMIT 1000;

-- 4. 

SELECT id,
       status,
       sale_id,
       policy_id,
       completed_at
FROM endpoint4.policy_completions
LIMIT 1000;

-- 5. 

SELECT id,
       status,
       sale_id,
       document_id,
       object_key,
       download_url,
       released_at
FROM endpoint5.document_releases
LIMIT 1000;