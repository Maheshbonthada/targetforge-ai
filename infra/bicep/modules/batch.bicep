@description('''Azure Batch account + autoscaling pool for real alignment (BWA-MEM2) and
variant-calling (GATK) compute — the two pipeline stages that need actual
reference-genome-scale processing this repo cannot exercise in a sandbox (see
bioinformatics/alignment.py and variant_calling.py docstrings,
docs/scaling-to-real-genomes.md).

Not deployed by default (see main.bicep''s `deployBatchCompute` switch): every
other resource in this stack costs nothing when idle, but a Batch pool's VM
nodes cost money the moment a node is allocated, even at zero utilization, so
this module is opt-in rather than something a `git clone` + first deploy pays
for by accident. Turn it on once there is a real reference genome, a real
BWA-MEM2/GATK container image in the registry, and a real sample to run.
''')
param location string
param namePrefix string
param environmentTag string
param autoStorageAccountId string

@description('Compute-optimized, no local SSD requirement met by the OS disk alone; BWA-MEM2/GATK are memory-hungry more than they are exotic-hardware-hungry. Resize once a real run's memory profile is known.')
param vmSize string = 'Standard_D8s_v5'

@description('Ceiling on simultaneously-allocated dedicated nodes — caps the worst-case runaway-cost scenario (e.g. a bug that resubmits the same job in a loop) independently of the autoscale formula.')
param maxDedicatedNodes int = environmentTag == 'prod' ? 10 : 2

resource batchAccount 'Microsoft.Batch/batchAccounts@2024-02-01' = {
  name: replace('${namePrefix}batch', '-', '')
  location: location
  tags: { environment: environmentTag, app: 'targetforge' }
  identity: { type: 'SystemAssigned' }
  properties: {
    poolAllocationMode: 'BatchService'
    autoStorageAccountId: autoStorageAccountId
    // Managed-identity-only job submission from the API (BatchContributor role
    // granted in main.bicep) — no Batch account keys anywhere.
    publicNetworkAccess: 'Enabled' // tighten to private endpoint before real study data — see compliance-checklist.md
  }
}

resource genomicsPool 'Microsoft.Batch/batchAccounts/pools@2024-02-01' = {
  parent: batchAccount
  name: 'genomics-compute-pool'
  properties: {
    vmSize: vmSize
    deploymentConfiguration: {
      virtualMachineConfiguration: {
        // Canonical's HPC-optimized image ships common genomics-adjacent
        // toolchain dependencies preinstalled; BWA-MEM2/GATK themselves are
        // pulled from the container registry via the pool's start task
        // (documented, not templated here — the exact image tag is a
        // deploy-time input, not infra) — see docs/scaling-to-real-genomes.md.
        imageReference: {
          publisher: 'microsoft-dsvm'
          offer: 'ubuntu-hpc'
          sku: '2204'
          version: 'latest'
        }
        nodeAgentSkuId: 'batch.node.ubuntu 22.04'
      }
    }
    // Scale-to-zero when idle (min target nodes = 0 whenever nothing is
    // queued), and spot/low-priority nodes: alignment and variant-calling are
    // both per-sample, checkpointable-by-retry jobs (a preempted task is
    // resubmitted, not lost), which is exactly the workload spot pricing (up
    // to ~80% off) exists for. See docs/cost-model.md.
    scaleSettings: {
      autoScale: {
        formula: '''
          maxNodes = ${maxDedicatedNodes};
          pendingTasks = $PendingTasks.GetSample(1);
          targetNodes = min(maxNodes, pendingTasks.GetLastSample());
          $TargetLowPriorityNodes = targetNodes;
          $TargetDedicatedNodes = 0;
          $NodeDeallocationOption = taskcompletion;
        '''
        evaluationInterval: 'PT5M'
      }
    }
    targetNodeCommunicationMode: 'Simplified'
  }
}

output accountName string = batchAccount.name
output accountEndpoint string = 'https://${batchAccount.properties.accountEndpoint}'
output principalId string = batchAccount.identity.principalId
output poolId string = genomicsPool.name
