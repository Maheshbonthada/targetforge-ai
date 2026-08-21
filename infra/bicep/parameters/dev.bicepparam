using '../main.bicep'

param environmentTag = 'dev'
param namePrefix = 'tf-dev'
param location = 'eastus2'

// Supplied at deploy time via GitHub Actions secret, never committed.
param postgresAdminPassword = ''

// Dev stays on the direct OpenAI key (see core/llm.py) until Azure OpenAI
// quota is provisioned — avoids paying for an idle Azure OpenAI deployment
// during early development.
param aiProvider = 'openai'

param deployBatchCompute = false

param entraTenantId = ''
param entraClientId = ''
param entraIssuer = ''
param entraAudience = ''
