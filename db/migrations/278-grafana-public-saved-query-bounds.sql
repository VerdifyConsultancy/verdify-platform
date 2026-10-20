-- #1002: public panels execute saved queries only. Bound each database statement
-- for this dedicated read-only login; applies when Grafana opens a new connection.
-- Does not grant any new relation access or affect ingestion/planner logins.
ALTER ROLE verdify_grafana_runtime_login SET statement_timeout = '15s';
ALTER ROLE verdify_grafana_runtime_login SET idle_in_transaction_session_timeout = '15s';
