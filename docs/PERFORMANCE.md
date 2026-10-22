# Local performance

Measurements on an Apple M4 Pro, macOS arm64, Go 1.27.3, on 2026-10-22. These are offline measurements, without a Wallapop account or external requests.

| Measurement | Observed result |
| --- | --- |
| Stripped native executable | 8,736,418 bytes (8.3 MiB) |
| Process launch through MCP initialize, 10 warm launches | Median 6.0 ms; range 4.7–19.5 ms |
| First observed initialize after building | 306 ms |
| Construct the default MCP server | 0.60 ms; about 853 KiB allocated |
| Read a cached 13-byte response | 501 ns; 1,008 bytes allocated |

Allocation totals are per operation, not resident memory. The cache benchmark includes copying the returned response. These figures are specific to this machine, build and workload; they do not measure live API throughput or browser login.

Reproduce the build, protocol check and microbenchmarks:

```sh
make build
python3 scripts/smoke_mcp.py ./bin/wallapop-mcp
make benchmark
```

The default upstream pacing is one request start per second, with a 45-second tool budget and at most four active tool calls. Cached responses avoid upstream requests. Latency for actual searches and account operations depends mostly on Wallapop, pagination and authentication.

The cache is limited to 128 entries and 32 MiB of response data. Upstream bodies are limited to 4 MiB; each tool output to 128 KiB. These limits bound individual components, not the process's total memory. Publishing can temporarily hold up to 32 MiB of input photos plus a multipart request buffer.
