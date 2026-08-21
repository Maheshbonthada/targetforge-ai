@description('TargetForge — resource-group-scoped deployment. One resource group per environment (dev/staging/prod); this template is applied to each independently via infra/bicep/parameters/<env>.bicepparam. See docs/runbook.md for the deploy sequence.')
targetScope = 'resourceGroup'

@allowed(['dev', 'staging', 'prod'])
param environmentTag string

param location string = resourceGroup().location

@description('Short, globally-unique-safe prefix, e.g. "tf-dev". Keep to <=12 chars — several resource types (Key Vault, Storage, ACR, Batch) have tight name-length limits.')
@maxLength(12)
param namePrefix string

@secure()
param postgresAdminPassword string

@description('Full image reference, e.g. <acr-login-server>/targetforge-api:<git-sha>. Left as a placeholder mcr image on first deploy so the environment stands up before CI has published anything — see docs/runbook.md.')
param containerImage string = 'mcr.microsoft.com/k8se/quickstart:latest'

@description('AI_PROVIDER for the deployed container. Stays "openai" (direct API, key supplied out-of-band via Key Vault) until Azure OpenAI quota is provisioned for this subscription, then flips to "azure_openai" — see core/llm.py.')
@allowed(['azure_openai', 'openai'])
param aiProvider string = 'azure_openai'

@description('Whether to deploy the Azure Batch account/pool for real alignment + variant-calling compute. Off by default — see modules/batch.bicep''s docstring for why this is the one resource in this stack that is opt-in rather than provisioned unconditionally.')
param deployBatchCompute bool = false

param entraTenantId string = ''
param entraClientId string = ''
param entraIssuer string = ''
param entraAudience string = ''

var tenantId = subscription().tenantId

// --- Foundational: observability, secrets, registry, storage ---

module logAnalytics 'modules/log-analytics.bicep' = {
  name: 'log-analytics'
  params: { location: location, namePrefix: namePrefix, environmentTag: environmentTag }
}

module keyVault 'modules/key-vault.bicep' = {
  name: 'key-vault'
  params: { location: location, namePrefix: namePrefix, environmentTag: environmentTag, tenantId: tenantId }
}

module registry 'modules/container-registry.bicep' = {
  name: 'container-registry'
  params: { location: location, namePrefix: namePrefix, environmentTag: environmentTag }
}

module storage 'modules/storage.bicep' = {
  name: 'storage'
  params: { location: location, namePrefix: namePrefix, environmentTag: environmentTag }
}

// --- Data plane ---

module postgres 'modules/postgres.bicep' = {
  name: 'postgres'
  params: {
    location: location
    namePrefix: namePrefix
    environmentTag: environmentTag
    administratorPassword: postgresAdminPassword
  }
}

// --- AI services ---

module openAi 'modules/openai.bicep' = {
  name: 'openai'
  params: { location: location, namePrefix: namePrefix, environmentTag: environmentTag }
}

// --- Real sequencing compute (opt-in — see modules/batch.bicep) ---

module batch 'modules/batch.bicep' = if (deployBatchCompute) {
  name: 'batch'
  params: {
    location: location
    namePrefix: namePrefix
    environmentTag: environmentTag
    autoStorageAccountId: storage.outputs.storageAccountId
  }
}

// --- Compute ---

module containerAppsEnv 'modules/container-apps-env.bicep' = {
  name: 'container-apps-env'
  params: {
    location: location
    namePrefix: namePrefix
    environmentTag: environmentTag
    logAnalyticsWorkspaceId: logAnalytics.outputs.workspaceId
    logAnalyticsCustomerId: reference(logAnalytics.outputs.workspaceId, '2023-09-01').customerId
    logAnalyticsSharedKey: listKeys(logAnalytics.outputs.workspaceId, '2023-09-01').primarySharedKey
  }
}

// The DB DSN is assembled here (never in application code or CI) and stored as a
// single Key Vault secret — the container app reads it via a Key-Vault-backed
// secretRef, so the connection string exists in exactly one place at rest.
resource keyVaultRef 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVault.outputs.keyVaultName
}

resource databaseUrlSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVaultRef
  name: 'database-url'
  properties: {
    value: 'postgresql+asyncpg://${'targetforge_admin'}:${postgresAdminPassword}@${postgres.outputs.serverFqdn}:5432/${postgres.outputs.databaseName}?ssl=require'
  }
}

module containerApp 'modules/container-app-api.bicep' = {
  name: 'container-app-api'
  params: {
    location: location
    namePrefix: namePrefix
    environmentTag: environmentTag
    containerAppsEnvironmentId: containerAppsEnv.outputs.environmentId
    containerImage: containerImage
    containerRegistryLoginServer: registry.outputs.loginServer
    keyVaultUri: keyVault.outputs.keyVaultUri
    databaseUrlSecretName: 'database-url'
    appInsightsConnectionString: logAnalytics.outputs.appInsightsConnectionString
    aiProvider: aiProvider
    openAiEndpoint: openAi.outputs.endpoint
    storageBlobEndpoint: storage.outputs.blobEndpoint
    batchAccountUrl: deployBatchCompute ? batch.outputs.accountEndpoint : ''
    batchPoolId: deployBatchCompute ? batch.outputs.poolId : ''
    ncbiToolEmail: 'research@targetforge.example' // replace with a real monitored inbox before prod — see runbook.md
    entraTenantId: entraTenantId
    entraClientId: entraClientId
    entraIssuer: entraIssuer
    entraAudience: entraAudience
    corsAllowedOrigins: 'http://localhost:5173' // no frontend deployed yet — see ARCHITECTURE.md open items
  }
  dependsOn: [databaseUrlSecret]
}

module migrationJob 'modules/migration-job.bicep' = {
  name: 'migration-job'
  params: {
    location: location
    namePrefix: namePrefix
    environmentTag: environmentTag
    containerAppsEnvironmentId: containerAppsEnv.outputs.environmentId
    containerImage: containerImage
    containerRegistryLoginServer: registry.outputs.loginServer
    keyVaultUri: keyVault.outputs.keyVaultUri
    databaseUrlSecretName: 'database-url'
  }
  dependsOn: [databaseUrlSecret]
}

// --- RBAC: least-privilege grants to each managed identity. No connection
// strings, API keys, or SAS tokens anywhere in this stack. ---

// Verify these against `az role definition list --name "<role name>"` before
// first deploy — built-in role GUIDs are stable but worth confirming rather
// than trusting from memory, especially batchContributor, which this repo
// has not yet deployed for real (see modules/batch.bicep).
var roleIds = {
  acrPull: '7f951dda-4ed3-4680-a7ca-43fe172d538d'
  keyVaultSecretsUser: '4633458b-17de-408a-b874-0445c86b69e6'
  storageBlobDataContributor: 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
  cognitiveServicesUser: 'a97b65f3-24c7-4388-baec-2e87135dc908'
  batchContributor: 'fbc52c3f-28ad-4303-a892-8a056630b8f1'
}

resource acrPullAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.outputs.registryId, containerApp.outputs.principalId, roleIds.acrPull)
  scope: resourceGroup()
  properties: {
    principalId: containerApp.outputs.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleIds.acrPull)
  }
}

resource kvSecretsAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.outputs.keyVaultId, containerApp.outputs.principalId, roleIds.keyVaultSecretsUser)
  scope: resourceGroup()
  properties: {
    principalId: containerApp.outputs.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleIds.keyVaultSecretsUser)
  }
}

resource storageAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.outputs.storageAccountId, containerApp.outputs.principalId, roleIds.storageBlobDataContributor)
  scope: resourceGroup()
  properties: {
    principalId: containerApp.outputs.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleIds.storageBlobDataContributor)
  }
}

resource openAiAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(openAi.outputs.accountName, containerApp.outputs.principalId, roleIds.cognitiveServicesUser)
  scope: resourceGroup()
  properties: {
    principalId: containerApp.outputs.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleIds.cognitiveServicesUser)
  }
}

resource migrateAcrPullAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.outputs.registryId, migrationJob.outputs.principalId, roleIds.acrPull)
  scope: resourceGroup()
  properties: {
    principalId: migrationJob.outputs.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleIds.acrPull)
  }
}

resource migrateKvSecretsAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.outputs.keyVaultId, migrationJob.outputs.principalId, roleIds.keyVaultSecretsUser)
  scope: resourceGroup()
  properties: {
    principalId: migrationJob.outputs.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleIds.keyVaultSecretsUser)
  }
}

// The API submits alignment/variant-calling jobs to Batch; Batch's own
// managed identity, in turn, needs to read/write the genomic-files container
// it was pointed at as auto-storage. Both grants only fire when the Batch
// module is actually deployed.
resource apiBatchAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (deployBatchCompute) {
  name: guid(batch.outputs.accountName, containerApp.outputs.principalId, roleIds.batchContributor)
  scope: resourceGroup()
  properties: {
    principalId: containerApp.outputs.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleIds.batchContributor)
  }
}

resource batchStorageAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (deployBatchCompute) {
  name: guid(storage.outputs.storageAccountId, batch.outputs.accountName, roleIds.storageBlobDataContributor)
  scope: resourceGroup()
  properties: {
    principalId: batch.outputs.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleIds.storageBlobDataContributor)
  }
}

output apiUrl string = 'https://${containerApp.outputs.fqdn}'
output containerRegistryLoginServer string = registry.outputs.loginServer
output keyVaultName string = keyVault.outputs.keyVaultName
