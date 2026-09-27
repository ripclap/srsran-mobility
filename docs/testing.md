# Testing

## Recorded runs

Containerlab 0.79.0 on ARM64 Docker/Colima, using clean source builds of the
patched srsRAN 4G stack and Open5GS 2.8.0. Both encrypted runs negotiated EEA2
ciphering and EIA2 integrity protection.

| Test | Handovers | Ping replies | Loss | Largest reply gap |
| --- | --- | --- | --- | --- |
| X2 forward, reverse, outage recovery | 3 | 607/608 | 0.164% | 210 ms |
| S1 forward and reverse | 2 | 229/230 | 0.435% | 120 ms |

The UE kept its IP address and did not reattach in either run. Measurements
cover the whole probe, including outage injection where applicable and probe
shutdown. Reply gaps are wall-clock gaps at a nominal 100 ms ping interval.

- [X2 results](../evidence/containerlab-x2-ciphered.json)
- [S1 results](../evidence/containerlab-s1-ciphered.json)
- [Channel and scenario tests](../evidence/channel-tests.txt): 13 passed

## Capture checks

The verifier uses tshark to decode network captures and checks:

- X2 Setup, Handover Request/Acknowledge, SN Status Transfer, and UE Context Release
- Required X2 IEs and criticalities against the R14.6.0 schema contract
- S1 Path Switch acknowledgements in X2 mode
- No S1 handover preparation or resource allocation in X2 mode
- S1 Handover Notify in S1 mode, with no X2 traffic
- Source context cleanup, UE IP continuity, and continued traffic

The X2 fault test drops target output SCTP traffic on port 36422. Both
preparation timers expire, the rule is removed, and another handover completes.
The verifier restores the rule in its cleanup path if a test fails.

Decoded signaling summaries:

- [X2 signaling](../evidence/containerlab-x2-ciphered-x2-wire.txt)
- [X2 run: S1 path switch](../evidence/containerlab-x2-ciphered-s1-wire.txt)
- [S1 signaling](../evidence/containerlab-s1-ciphered-wire.txt)

Capture hashes are included in the result JSON. Raw captures, full stack logs,
and generated subscriber data stay under the ignored `build/` directory.

## Source checks

[Source manifest](../evidence/source-manifest.json) records the upstream revision,
patch hash, and changed native files. Existing native tests were left intact.

Optional noise, delay, Doppler, and fading models are covered by fixtures.
Inter-MME mobility, third-party X2 peers, and multi-UE load need separate testing.
