@description('Azure OpenAI account with two model deployments, matching core/llm.py''s fast/reasoning split: a cheap/fast model for the high-volume summarization agents (literature, druggability, competitive intel, QC, annotation), and a stronger model reserved for the two agents that actually weigh evidence and write prose (prioritization, report generation). Model routing by task, not one model for everything, is the single biggest lever on per-request cost — see docs/cost-model.md.')
param location string
param namePrefix string
param environmentTag string

@description('Check current model/version availability for your region in the Azure OpenAI docs before deploying — these rotate.')
param fastModelName string = 'gpt-4o-mini'
param fastModelVersion string = '2024-07-18'
param reasoningModelName string = 'gpt-4o'
param reasoningModelVersion string = '2024-08-06'

resource account 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: '${namePrefix}-aoai'
  location: location
  tags: { environment: environmentTag, app: 'targetforge' }
  kind: 'OpenAI'
  sku: { name: 'S0' }
  identity: { type: 'SystemAssigned' }
  properties: {
    customSubDomainName: '${namePrefix}-aoai'
    publicNetworkAccess: 'Enabled' // tighten to private endpoint before real study data — see compliance-checklist.md
    disableLocalAuth: true // managed-identity/Entra auth only, no API keys to leak
  }
}

resource fastDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: account
  name: fastModelName
  sku: {
    name: 'Standard'
    capacity: 20 // TPM in units of 1000; comfortably covers literature/druggability/competitive summarization across several concurrent runs
  }
  properties: {
    model: { format: 'OpenAI', name: fastModelName, version: fastModelVersion }
    versionUpgradeOption: 'OnceNewDefaultVersionAvailable'
  }
}

resource reasoningDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: account
  name: reasoningModelName
  sku: {
    name: 'Standard'
    capacity: 10
  }
  properties: {
    model: { format: 'OpenAI', name: reasoningModelName, version: reasoningModelVersion }
    versionUpgradeOption: 'OnceNewDefaultVersionAvailable'
  }
  dependsOn: [fastDeployment] // Cognitive Services deployments serialize; avoids a rare provisioning race
}

output endpoint string = account.properties.endpoint
output accountName string = account.name
output principalId string = account.identity.principalId
