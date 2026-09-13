using './main.bicep'

// --- Fill these in before deploying (see infra/README.md phase 0/1) ---

param location = 'westus' // avoid 'eastus' — Operations Agent isn't available there
param resourceGroupName = 'rg-fleet-ops-copilot'

// At least one Fabric capacity admin UPN. Include whoever will run
// setup/06_ops_agent.py — the Operations Agent runs under its creator's identity.
param fabricAdminMembers = [
  'pranabp@MngEnvMCAP072730.onmicrosoft.com'
]

// az ad signed-in-user show --query id -o tsv
param operatorPrincipalId = '3329059e-d368-43cf-85e2-44691c1c1bbc'

param fabricCapacityName = 'fleetopsf8'

param foundryAiServicesBaseName = 'fleetopsai'
param foundryProjectName = 'fleet-incident'
param foundryModelName = 'gpt-4.1'
param foundryModelFormat = 'OpenAI'
param foundryModelVersion = '2025-04-14'
param foundryModelSkuName = 'GlobalStandard'
param foundryModelCapacity = 30

// Created by wave0-byo-resources.bicep (deployment name: wave0-byo-deployment).
param aiSearchResourceId = '/subscriptions/f26d977d-4a4e-45b3-b4a8-68d268c44852/resourceGroups/rg-fleet-ops-copilot/providers/Microsoft.Search/searchServices/srch-fleetops-4fgm3np6kyfvg'
param azureStorageAccountResourceId = '/subscriptions/f26d977d-4a4e-45b3-b4a8-68d268c44852/resourceGroups/rg-fleet-ops-copilot/providers/Microsoft.Storage/storageAccounts/stfleetops4fgm3np6kyfvg'
param azureCosmosDBAccountResourceId = '/subscriptions/f26d977d-4a4e-45b3-b4a8-68d268c44852/resourceGroups/rg-fleet-ops-copilot/providers/Microsoft.DocumentDB/databaseAccounts/cosmos-fleetops-4fgm3np6kyfvg'
