## Infrastructure as Code (CloudFormation)

`cloudformation/vpc-peering.yaml` rebuilds this project in ap-southeast-2: two VPCs, a peering connection with routes in both directions, a bastion, and private App-A and App-B instances. See [`cloudformation/README.md`](cloudformation/README.md) for deployment, outputs and cleanup. The template has not yet been deployed or tested.
