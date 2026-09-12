@description('Location for the VNet and its subnets.')
param location string

@description('VNet name.')
param vnetName string = 'vnet-fleetops'

param vnetAddressPrefix string = '10.10.0.0/16'

@description('Private endpoints subnet — Fabric tenant/workspace PL, Key Vault, and the Foundry BYO trio all land here.')
param peSubnetPrefix string = '10.10.0.0/24'
param peSubnetName string = 'snet-pe'

@description('Foundry agent subnet — must stay delegated to Microsoft.App/environments and dedicated to one Foundry account.')
param agentSubnetPrefix string = '10.10.1.0/24'
param agentSubnetName string = 'snet-agent'

param bastionSubnetPrefix string = '10.10.2.0/26'

param jumpboxSubnetPrefix string = '10.10.2.64/27'
param jumpboxSubnetName string = 'snet-jumpbox'

@description('Container Instance subnet — delegated to Microsoft.ContainerInstance/containerGroups. Hosts an ACI jumpbox reachable via `az container exec` (Azure control plane, no RDP/Bastion needed), mirroring pranabpaul-tech/foundry-iq-v2\'s pattern.')
param containerSubnetPrefix string = '10.10.3.0/24'
param containerSubnetName string = 'snet-container'

resource vnet 'Microsoft.Network/virtualNetworks@2023-11-01' = {
  name: vnetName
  location: location
  properties: {
    addressSpace: {
      addressPrefixes: [vnetAddressPrefix]
    }
    subnets: [
      {
        name: peSubnetName
        properties: {
          addressPrefix: peSubnetPrefix
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
      {
        name: agentSubnetName
        properties: {
          addressPrefix: agentSubnetPrefix
          delegations: [
            {
              name: 'foundryAgentDelegation'
              properties: {
                serviceName: 'Microsoft.App/environments'
              }
            }
          ]
        }
      }
      {
        // Name is fixed by Azure Bastion — do not rename.
        name: 'AzureBastionSubnet'
        properties: {
          addressPrefix: bastionSubnetPrefix
        }
      }
      {
        name: jumpboxSubnetName
        properties: {
          addressPrefix: jumpboxSubnetPrefix
        }
      }
      {
        name: containerSubnetName
        properties: {
          addressPrefix: containerSubnetPrefix
          delegations: [
            {
              name: 'aciDelegation'
              properties: {
                serviceName: 'Microsoft.ContainerInstance/containerGroups'
              }
            }
          ]
        }
      }
    ]
  }
}

output vnetId string = vnet.id
output vnetName string = vnet.name
output peSubnetId string = vnet.properties.subnets[0].id
output peSubnetName string = peSubnetName
output agentSubnetId string = vnet.properties.subnets[1].id
output agentSubnetName string = agentSubnetName
output bastionSubnetId string = vnet.properties.subnets[2].id
output jumpboxSubnetId string = vnet.properties.subnets[3].id
output jumpboxSubnetName string = jumpboxSubnetName
output containerSubnetId string = vnet.properties.subnets[4].id
output containerSubnetName string = containerSubnetName
