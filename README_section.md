## My implementation: VPC Peering in Sydney (ap-southeast-2)

**Author:** Rudra Sanjay Rakshe | **Evidence captured:** 28 October 2026
**Full report:** `RudraRakshe_EWB_VPC_Peering.pdf`

I connected two VPCs with a peering connection and tested private communication between EC2 instances (Amazon Linux 2023, t3.micro).

| Resource | Value |
|---|---|
| VPC A `ewb-vpc-a` | `10.10.0.0/16` (public `10.10.1.0/24`, private `10.10.2.0/24`) |
| VPC B `ewb-vpc-b` | `10.20.0.0/16` (private `10.20.1.0/24`) |
| Peering connection | `pcx-04950eee78a68adf1` |
| Bastion (final) | `i-044f460b5fb67b887`, private `10.10.1.10` |
| App-A / App-B | `10.10.2.10` / `10.20.1.10`, no public IPv4 |

### Verified results (with screenshots in the report)

| Test | Result |
|---|---|
| Test 1: baseline failure before peering | **Not verified.** No pre-peering evidence captured |
| Test 2: App-A to App-B ping | Passed, 4/4 replies, 0% loss |
| Test 3: App-B to App-A ping | Passed, 4/4 replies, 0% loss |
| Test 4: private SSH via bastion | Passed (App-B `10.20.1.10`) |
| Test 5: no public IPv4 on App-A / App-B | Passed |
| Controlled ICMP failure and recovery | 100% loss, then 0% loss shown; security-group rule edits were not screenshotted |
| Cleanup | Instances terminated, no EBS volumes, peering connection deleted. VPCs, subnets, route tables, IGW and security groups not evidenced |

### Notes
- The bastion was replaced during troubleshooting; its SSH source was temporarily `0.0.0.0/0` because my mobile-hotspot IP kept changing.
- Security groups are stateful, so a deleted inbound ICMP rule only affects pings started by the other side.
- Details, limits of the evidence and lessons learned are in the report.
