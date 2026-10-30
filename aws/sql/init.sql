-- Ejecutar una sola vez con psql como lab_admin, TLS verify-full y CA regional.
-- Las contraseñas se solicitan sin guardarlas en este archivo ni en argumentos.
\set ON_ERROR_STOP on
CREATE ROLE lab_general LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
\password lab_general
CREATE ROLE lab_terceros LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
\password lab_terceros
CREATE ROLE lab_onprem LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
\password lab_onprem
CREATE DATABASE lab_general;
CREATE DATABASE lab_terceros;
CREATE DATABASE lab_onprem;
REVOKE ALL ON DATABASE lab_general, lab_terceros, lab_onprem FROM PUBLIC;
GRANT CONNECT ON DATABASE lab_general TO lab_general;
GRANT CONNECT ON DATABASE lab_terceros TO lab_terceros;
GRANT CONNECT ON DATABASE lab_onprem TO lab_onprem;
\connect lab_general
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
\ir ../../src/main/resources/schema.sql
GRANT USAGE ON SCHEMA endpoint0, endpoint1, endpoint2, endpoint3, endpoint4, endpoint5 TO lab_general;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA endpoint0, endpoint1, endpoint2, endpoint3, endpoint4, endpoint5 TO lab_general;
\connect lab_terceros
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
\ir ../../src/main/resources/schema.sql
GRANT USAGE ON SCHEMA endpoint0, endpoint1, endpoint2, endpoint3, endpoint4, endpoint5 TO lab_terceros;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA endpoint0, endpoint1, endpoint2, endpoint3, endpoint4, endpoint5 TO lab_terceros;
\connect lab_onprem
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
\ir ../../src/main/resources/schema.sql
GRANT USAGE ON SCHEMA endpoint0, endpoint1, endpoint2, endpoint3, endpoint4, endpoint5 TO lab_onprem;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA endpoint0, endpoint1, endpoint2, endpoint3, endpoint4, endpoint5 TO lab_onprem;
