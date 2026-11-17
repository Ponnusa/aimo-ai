-- Creates three isolated databases: network, buyer, supplier.
-- No cross-database foreign keys or views are ever added here, by design:
-- see docs/ARCHITECTURE.md ("No cross-organization SQL joins").
CREATE DATABASE aimo_network;
CREATE DATABASE aimo_buyer;
CREATE DATABASE aimo_supplier;
