// Required, not optional: once the Fabric workspace and Key Vault deny public
// access, this VM + Bastion is the only way to run the Python setup scripts and
// reach the portal for the one-time Eventstream / Operations Agent authoring steps.

@description('Location for the jumpbox VM and Bastion host.')
param location string

param bastionSubnetId string
param jumpboxSubnetId string

param vmName string = 'vm-fleetops-jump'
// Windows computer names are capped at 15 characters (NetBIOS) — vmName (the
// Azure resource name) can be longer and more descriptive, so this is a
// separate, shorter value rather than reusing vmName. Hit this as a real
// deploy-time failure (osProfile.computerName), not caught by `az deployment
// group validate`, which doesn't check this constraint.
@maxLength(15)
param computerName string = 'vm-fleetops-jmp'
// Standard_D2s_v5 hit a live capacity restriction in westus. Switched to
// Standard_D2s_v7 + Windows Server (see imageReference below) — both the size
// and the server-not-client OS choice match a combination independently
// validated in a similar Microsoft-internal tenant by
// github.com/anihitk07/foundry-hosted-agents-e2e-samples.
param vmSize string = 'Standard_D2s_v7'

@description('Local admin username for the jumpbox.')
param adminUsername string

@secure()
@description('Local admin password for the jumpbox. Prefer generating this and storing it in Key Vault rather than passing a literal.')
param adminPassword string

resource nic 'Microsoft.Network/networkInterfaces@2023-11-01' = {
  name: '${vmName}-nic'
  location: location
  properties: {
    ipConfigurations: [
      {
        name: 'ipconfig1'
        properties: {
          subnet: {
            id: jumpboxSubnetId
          }
          privateIPAllocationMethod: 'Dynamic'
        }
      }
    ]
  }
}

resource vm 'Microsoft.Compute/virtualMachines@2024-03-01' = {
  name: vmName
  location: location
  properties: {
    hardwareProfile: {
      vmSize: vmSize
    }
    osProfile: {
      computerName: computerName
      adminUsername: adminUsername
      adminPassword: adminPassword
    }
    storageProfile: {
      imageReference: {
        publisher: 'MicrosoftWindowsServer'
        offer: 'WindowsServer'
        sku: '2022-datacenter-azure-edition'
        version: 'latest'
      }
      osDisk: {
        createOption: 'FromImage'
        managedDisk: {
          storageAccountType: 'Premium_LRS'
        }
      }
    }
    networkProfile: {
      networkInterfaces: [
        {
          id: nic.id
        }
      ]
    }
  }
}

resource bastionPip 'Microsoft.Network/publicIPAddresses@2023-11-01' = {
  name: 'pip-fleetops-bastion'
  location: location
  sku: {
    name: 'Standard'
  }
  properties: {
    publicIPAllocationMethod: 'Static'
  }
}

resource bastion 'Microsoft.Network/bastionHosts@2023-11-01' = {
  name: 'bas-fleetops'
  location: location
  sku: {
    name: 'Standard'
  }
  properties: {
    ipConfigurations: [
      {
        name: 'ipconfig1'
        properties: {
          subnet: {
            id: bastionSubnetId
          }
          publicIPAddress: {
            id: bastionPip.id
          }
        }
      }
    ]
  }
}

output vmId string = vm.id
output vmName string = vm.name
output bastionId string = bastion.id
