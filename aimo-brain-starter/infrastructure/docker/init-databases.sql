-- Creates six isolated databases: network, registry, buyer, supplier,
-- buyer-mcp, supplier-mcp. No cross-database foreign keys or views are
-- ever added here, by design: see docs/ARCHITECTURE.md ("No
-- cross-organization SQL joins").
CREATE DATABASE aimo_network;
CREATE DATABASE aimo_registry;
CREATE DATABASE aimo_buyer;
CREATE DATABASE aimo_supplier;
CREATE DATABASE aimo_buyer_mcp;
CREATE DATABASE aimo_supplier_mcp;
