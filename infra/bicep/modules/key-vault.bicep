@description('Key Vault holding the Postgres admin password and any other secrets. RBAC-authorized, not access-policy-authorized — access-policy is the legacy model and grants too coarsely.')
param location string
param namePrefix string
param environmentTag string
param tenantId string

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  // Key Vault names must be globally unique and <=24 chars; namePrefix is kept short for this reason.
  name: '${namePrefix}-kv'
  location: location
  tags: { environment: environmentTag, app: 'targetforge' }
  properties: {
    tenantId: tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    enablePurgeProtection: environmentTag == 'prod' ? true : null
  }
}

output keyVaultId string = keyVault.id
output keyVaultUri string = keyVault.properties.vaultUri
output keyVaultName string = keyVault.name
