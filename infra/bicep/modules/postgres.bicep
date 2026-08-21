@description('Azure Database for PostgreSQL Flexible Server. Burstable tier — this workload is run metadata (candidate genes, stage status, the final JSON report), not the genomic files themselves; a handful of R&D teams running occasional pipeline runs does not need General Purpose. Right-size up from actual metrics, not guesswork. See docs/cost-model.md.')
param location string
param namePrefix string
param environmentTag string
param administratorLogin string = 'targetforge_admin'
@secure()
param administratorPassword string
param databaseName string = 'targetforge'

var skuName = environmentTag == 'prod' ? 'Standard_B2s' : 'Standard_B1ms'
var storageSizeGb = environmentTag == 'prod' ? 64 : 32

resource server 'Microsoft.DBforPostgreSQL/flexibleServers@2023-06-01-preview' = {
  name: '${namePrefix}-pg'
  location: location
  tags: { environment: environmentTag, app: 'targetforge' }
  sku: { name: skuName, tier: 'Burstable' }
  properties: {
    version: '16'
    administratorLogin: administratorLogin
    administratorLoginPassword: administratorPassword
    storage: { storageSizeGB: storageSizeGb }
    backup: {
      backupRetentionDays: environmentTag == 'prod' ? 14 : 7
      geoRedundantBackup: environmentTag == 'prod' ? 'Enabled' : 'Disabled'
    }
    highAvailability: { mode: 'Disabled' } // enable ZoneRedundant once uptime SLA is a paying-customer requirement
  }
}

resource database 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2023-06-01-preview' = {
  parent: server
  name: databaseName
  properties: {
    charset: 'UTF8'
    collation: 'en_US.utf8'
  }
}

// Allows Azure-hosted services (Container Apps consumption plan, no static
// outbound IP by default) to reach the server. Narrow this to the Container
// Apps environment's outbound IPs (or move to VNet integration + private
// access) before real study data — see docs/compliance-checklist.md.
resource allowAzureServices 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2023-06-01-preview' = {
  parent: server
  name: 'AllowAzureServices'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

output serverFqdn string = server.properties.fullyQualifiedDomainName
output databaseName string = database.name
