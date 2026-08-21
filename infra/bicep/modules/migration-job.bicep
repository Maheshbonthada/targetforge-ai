@description('A Container Apps Job that runs `alembic upgrade head` on demand. Migrations run identically in every environment through this job — never by hand against a live DSN from someone''s laptop.')
param location string
param namePrefix string
param environmentTag string
param containerAppsEnvironmentId string
param containerImage string
param containerRegistryLoginServer string
param keyVaultUri string
param databaseUrlSecretName string

resource migrationJob 'Microsoft.App/jobs@2024-03-01' = {
  name: '${namePrefix}-migrate'
  location: location
  tags: { environment: environmentTag, app: 'targetforge' }
  identity: { type: 'SystemAssigned' }
  properties: {
    environmentId: containerAppsEnvironmentId
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 300
      replicaRetryLimit: 1
      registries: [
        { server: containerRegistryLoginServer, identity: 'system' }
      ]
      secrets: [
        {
          name: 'database-url'
          keyVaultUrl: '${keyVaultUri}secrets/${databaseUrlSecretName}'
          identity: 'system'
        }
      ]
      manualTriggerConfig: { parallelism: 1, replicaCompletionCount: 1 }
    }
    template: {
      containers: [
        {
          name: 'migrate'
          image: containerImage
          command: ['alembic', 'upgrade', 'head']
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: [
            { name: 'DATABASE_URL', secretRef: 'database-url' }
          ]
        }
      ]
    }
  }
}

output principalId string = migrationJob.identity.principalId
