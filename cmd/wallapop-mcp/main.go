// Command wallapop-mcp serves the Wallapop tools over MCP stdio.
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/jrrome/wallapop-mcp/internal/mcpserver"
	"github.com/jrrome/wallapop-mcp/internal/transport"
	"github.com/jrrome/wallapop-mcp/internal/wallapop"
	"github.com/modelcontextprotocol/go-sdk/mcp"
)

var version = "dev"

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	if err := run(ctx, os.Args[1:], os.Stderr); err != nil && !errors.Is(err, context.Canceled) {
		fmt.Fprintln(os.Stderr, "wallapop-mcp:", err)
		os.Exit(1)
	}
}

func run(ctx context.Context, args []string, stderr io.Writer) error {
	flags := flag.NewFlagSet("wallapop-mcp", flag.ContinueOnError)
	flags.SetOutput(stderr)
	writes := flags.Bool("allow-writes", false, "Expose mutation tools and permit account/listing changes")
	session := flags.String("session", os.Getenv("WALLAPOP_SESSION_PATH"), "Session JSON file or Python CLI configuration directory (optional)")
	interval := flags.Duration("min-interval", time.Second, "Minimum time between upstream requests")
	timeout := flags.Duration("timeout", 45*time.Second, "Total time budget per tool call")
	concurrency := flags.Int("concurrency", 4, "Maximum simultaneous tool calls")
	showVersion := flags.Bool("version", false, "Print version and exit")
	if err := flags.Parse(args); err != nil {
		if errors.Is(err, flag.ErrHelp) {
			return nil
		}
		return err
	}
	if flags.NArg() != 0 {
		return errors.New("unexpected positional arguments")
	}
	if *showVersion {
		fmt.Fprintln(stderr, "wallapop-mcp", version)
		return nil
	}
	if *interval < 0 || *timeout <= 0 || *concurrency < 1 || *concurrency > 16 {
		return errors.New("interval must be non-negative, timeout positive, concurrency between 1 and 16")
	}
	httpClient, err := transport.New(transport.Config{MinInterval: *interval, Timeout: *timeout, MaxAttempts: 3, CacheEntries: 128, MaxBodyBytes: 4 << 20})
	if err != nil {
		return err
	}
	defer httpClient.Close()
	client, err := wallapop.New(wallapop.Config{Transport: httpClient, AccessToken: os.Getenv("WALLAPOP_ACCESS_TOKEN"), RefreshToken: os.Getenv("WALLAPOP_REFRESH_TOKEN"), SessionPath: *session, EnableWrites: *writes})
	if err != nil {
		return err
	}
	server, err := mcpserver.New(client, mcpserver.Options{Version: version, EnableWrites: *writes, Timeout: *timeout, MaxConcurrent: *concurrency})
	if err != nil {
		return err
	}
	return server.Run(ctx, &mcp.StdioTransport{MaxLineLength: 64 << 10})
}
