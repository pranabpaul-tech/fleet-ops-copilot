// Fleet Ops Copilot — Wave 1
// Network, F8 Fabric capacity, tenant-level Fabric private link, Key Vault,
// monitoring, jumpbox + Bastion, and the VNet-injected Foundry account/project.
//
// Deliberately NOT included here (see infra/README.md):
//   - Fabric workspace, Eventhouse, Eventstream, Operations Agent — these are
//     Fabric workspace items, not ARM resources. Run src/fleetops/setup/*.py
//     after this deployment finishes.
//   - Workspace-level Fabric private link (infra/wave2-fabric-privatelink.bicep)
//     — needs a workspace ID that only exists after setup/01_workspace.py runs.
//   - The bot service + Teams channel (infra/wave3-bot.bicep) — needs the
//     Foundry agent's own client ID, which only exists after
//     foundry/deploy_hosted_agent.py runs.
//   - The Foundry capability host — run foundry-vendored/createCapHost.sh
//     after this deployment.
//
// Tenant prerequisites this can't do for you (Fabric admin portal, manual):
// register the Microsoft.Fabric / Microsoft.BotService / Microsoft.App resource
// providers, enable "Azure Private Link" and "Configure workspace-level inbound
// network rules" tenant settings, and enable Copilot / Azure OpenAI tenant
// settings the Operations Agent depends on.

targetScope = 'subscription'

@description('Azure region for every resource in this deployment. Must support Fabric capacities, Foundry VNet injection, and your chosen model. Avoid East US — the Operations Agent is not available there.')
param location string

@description('Name of the resource group to create.')
param resourceGroupName string = 'rg-fleet-ops-copilot'

@description('UPNs of the Fabric capacity administrators. Include the account that will run setup/06_ops_agent.py — the Operations Agent inherits its creator\'s delegated identity.')
param fabricAdminMembers array

@description('Object ID of the operator who should get Key Vault Secrets Officer on this resource group (typically the person running the Python setup scripts from the jumpbox).')
param operatorPrincipalId string

param fabricCapacityName string = 'fleetopsf8'

@description('Tenant-level Fabric private link requires tenant-administrator rights to create the Microsoft.PowerBI/privateLinkServicesForPowerBI resource. In a managed/corporate tenant where you only have subscription-level access, this fails with "forbidden — tenant administrator only". Default false so the deploy proceeds on workspace-level private link alone (Wave 2) — the documented fallback posture. Flip to true once a tenant admin has enabled the "Azure Private Link" tenant setting and granted the needed rights.')
param deployTenantPrivateLink bool = false

param jumpboxAdminUsername string
@secure()
param jumpboxAdminPassword string

param foundryAiServicesBaseName string = 'fleetopsai'
param foundryProjectName string = 'fleet-incident'
param foundryModelName string = 'gpt-4.1'
param foundryModelFormat string = 'OpenAI'
param foundryModelVersion string = '2025-04-14'
param foundryModelSkuName string = 'GlobalStandard'
param foundryModelCapacity int = 30

@description('Existing AI Search resource ID for the Foundry standard agent setup (BYO trio — required).')
param aiSearchResourceId string

@description('Existing Storage account resource ID for the Foundry standard agent setup.')
param azureStorageAccountResourceId string

@description('Existing Cosmos DB account resource ID for the Foundry standard agent setup.')
param azureCosmosDBAccountResourceId string

var keyVaultSecretsOfficerRoleId = 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7'

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: resourceGroupName
  location: location
}

module network 'modules/network.bicep' = {
  name: 'network-deployment'
  scope: rg
  params: {
    location: location
  }
}

module capacity 'modules/fabric-capacity.bicep' = {
  name: 'fabric-capacity-deployment'
  scope: rg
  params: {
    location: location
    capacityName: fabricCapacityName
    adminMembers: fabricAdminMembers
  }
}

module tenantPrivateLink 'modules/fabric-tenant-privatelink.bicep' = if (deployTenantPrivateLink) {
  name: 'fabric-tenant-pl-deployment'
  scope: rg
  params: {
    location: location
    vnetId: network.outputs.vnetId
    peSubnetId: network.outputs.peSubnetId
  }
}

module keyVault 'modules/keyvault.bicep' = {
  name: 'keyvault-deployment'
  scope: rg
  params: {
    location: location
    vnetId: network.outputs.vnetId
    peSubnetId: network.outputs.peSubnetId
  }
}

module monitoring 'modules/monitoring.bicep' = {
  name: 'monitoring-deployment'
  scope: rg
  params: {
    location: location
  }
}

module jumpbox 'modules/jumpbox.bicep' = {
  name: 'jumpbox-deployment'
  scope: rg
  params: {
    location: location
    bastionSubnetId: network.outputs.bastionSubnetId
    jumpboxSubnetId: network.outputs.jumpboxSubnetId
    adminUsername: jumpboxAdminUsername
    adminPassword: jumpboxAdminPassword
  }
}

module foundry 'modules/foundry.bicep' = {
  name: 'foundry-deployment'
  scope: rg
  params: {
    location: location
    aiServicesBaseName: foundryAiServicesBaseName
    firstProjectName: foundryProjectName
    modelName: foundryModelName
    modelFormat: foundryModelFormat
    modelVersion: foundryModelVersion
    modelSkuName: foundryModelSkuName
    modelCapacity: foundryModelCapacity
    vnetResourceId: network.outputs.vnetId
    agentSubnetName: network.outputs.agentSubnetName
    peSubnetName: network.outputs.peSubnetName
    aiSearchResourceId: aiSearchResourceId
    azureStorageAccountResourceId: azureStorageAccountResourceId
    azureCosmosDBAccountResourceId: azureCosmosDBAccountResourceId
  }
}

module operatorKvAccess 'modules/rbac.bicep' = {
  name: 'operator-kv-rbac-deployment'
  scope: rg
  params: {
    principalId: operatorPrincipalId
    roleDefinitionId: keyVaultSecretsOfficerRoleId
    principalType: 'User'
  }
}

output resourceGroupName string = rg.name
output vnetId string = network.outputs.vnetId
output vnetName string = network.outputs.vnetName
output agentSubnetId string = network.outputs.agentSubnetId
output agentSubnetName string = network.outputs.agentSubnetName
output peSubnetId string = network.outputs.peSubnetId
output peSubnetName string = network.outputs.peSubnetName
output bastionSubnetId string = network.outputs.bastionSubnetId
output jumpboxSubnetId string = network.outputs.jumpboxSubnetId
output jumpboxVmName string = jumpbox.outputs.vmName
output fabricCapacityId string = capacity.outputs.capacityId
output fabricCapacityName string = capacity.outputs.capacityName
output keyVaultName string = keyVault.outputs.vaultName
output keyVaultUri string = keyVault.outputs.vaultUri
output logAnalyticsWorkspaceId string = monitoring.outputs.workspaceId
output appInsightsConnectionString string = monitoring.outputs.connectionString
output foundryAccountName string = foundry.outputs.accountName
output foundryAccountId string = foundry.outputs.accountId
output foundryAccountEndpoint string = foundry.outputs.accountEndpoint
output foundryProjectName string = foundry.outputs.projectName
output foundryProjectId string = foundry.outputs.projectId
