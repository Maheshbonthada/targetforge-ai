using '../main.bicep'

param environmentTag = 'prod'
param namePrefix = 'tf-prod'
param location = 'eastus2'

// Supplied at deploy time via GitHub Actions secret, never committed.
param postgresAdminPassword = ''

param aiProvider = 'azure_openai'

// Flip to true once a real reference genome + BWA-MEM2/GATK container image
// exist to run against — see modules/batch.bicep.
param deployBatchCompute = false

param entraTenantId = ''
param entraClientId = ''
param entraIssuer = ''
param entraAudience = ''
