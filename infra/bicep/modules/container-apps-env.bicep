@description('Container Apps Environment on the Consumption plan — scale-to-zero when idle, pay per vCPU-second/GiB-second under load. Right choice pre-revenue: zero fixed compute cost while nobody is running a pipeline, still autoscales cleanly under real load on the same environment.')
param location string
param namePrefix string
param environmentTag string
param logAnalyticsWorkspaceId string
param logAnalyticsCustomerId string
@secure()
param logAnalyticsSharedKey string

resource containerAppsEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${namePrefix}-cae'
  location: location
  tags: { environment: environmentTag, app: 'targetforge' }
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalyticsCustomerId
        sharedKey: logAnalyticsSharedKey
      }
    }
    workloadProfiles: [
      { name: 'Consumption', workloadProfileType: 'Consumption' }
    ]
  }
}

output environmentId string = containerAppsEnv.id
