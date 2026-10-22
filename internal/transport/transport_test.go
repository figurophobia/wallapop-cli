package transport

import (
	"context"
	"errors"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func newTestClient(t *testing.T, cfg Config) *Client {
	t.Helper()
	if cfg.Timeout == 0 {
		cfg.Timeout = 2 * time.Second
	}
	c, err := New(cfg)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(c.Close)
	return c
}

func get(t *testing.T, c *Client, url string) (Response, error) {
	t.Helper()
	return c.Do(context.Background(), Request{URL: url, ReadOnly: true})
}

func testError(t *testing.T, err error, kind string) *Error {
	t.Helper()
	var e *Error
	if !errors.As(err, &e) || e.Kind != kind {
		t.Fatalf("want %s error, got %v", kind, err)
	}
	return e
}

func TestConnectionReuse(t *testing.T) {
	var connections atomic.Int32
	s := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { fmt.Fprint(w, "ok") }))
	s.Config.ConnState = func(_ net.Conn, state http.ConnState) {
		if state == http.StateNew {
			connections.Add(1)
		}
	}
	s.Start()
	defer s.Close()
	c := newTestClient(t, Config{})
	for range 3 {
		if _, err := get(t, c, s.URL); err != nil {
			t.Fatal(err)
		}
	}
	if connections.Load() != 1 {
		t.Fatalf("connections = %d", connections.Load())
	}
}

func TestRetryAfterAndDeadline(t *testing.T) {
	var calls atomic.Int32
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		w.Header().Set("Retry-After", "120")
		w.WriteHeader(429)
	}))
	defer s.Close()
	c := newTestClient(t, Config{Timeout: 80 * time.Millisecond})
	start := time.Now()
	_, err := get(t, c, s.URL)
	testError(t, err, "timeout")
	if calls.Load() != 1 || time.Since(start) > 500*time.Millisecond {
		t.Fatalf("calls=%d elapsed=%s", calls.Load(), time.Since(start))
	}
	if got := parseRetryAfter(time.Now().Add(time.Hour).UTC().Format(http.TimeFormat)); got < 59*time.Minute {
		t.Fatalf("Retry-After must never be shortened: %s", got)
	}
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatal("deadline cause lost")
	}
	if got := parseRetryAfter("120"); got != 120*time.Second {
		t.Fatalf("Retry-After = %s", got)
	}
}

func TestReadRetryOnlyAndNo403Retry(t *testing.T) {
	for _, tc := range []struct {
		name     string
		status   int
		readOnly bool
		method   string
		want     int
	}{
		{"read503", 503, true, http.MethodGet, 2},
		{"write503", 503, false, http.MethodPost, 1},
		{"getWrite503", 503, false, http.MethodGet, 1},
		{"read403", 403, true, http.MethodGet, 1},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var calls atomic.Int32
			s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if calls.Add(1) == 1 {
					w.WriteHeader(tc.status)
					return
				}
				fmt.Fprint(w, "ok")
			}))
			defer s.Close()
			c := newTestClient(t, Config{MaxAttempts: 2})
			_, err := c.Do(context.Background(), Request{URL: s.URL, Method: tc.method, ReadOnly: tc.readOnly})
			if tc.want == 1 {
				testError(t, err, "http")
			} else if err != nil {
				t.Fatal(err)
			}
			if int(calls.Load()) != tc.want {
				t.Fatalf("calls=%d", calls.Load())
			}
		})
	}
}

func TestGETWriteNeverCached(t *testing.T) {
	var calls atomic.Int32
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { calls.Add(1); fmt.Fprint(w, "ok") }))
	defer s.Close()
	c := newTestClient(t, Config{})
	for range 2 {
		if _, err := c.Do(context.Background(), Request{URL: s.URL, Method: http.MethodGet, CacheTTL: time.Hour}); err != nil {
			t.Fatal(err)
		}
	}
	if calls.Load() != 2 {
		t.Fatalf("calls=%d", calls.Load())
	}
}

func TestCancellationWhilePacing(t *testing.T) {
	var calls atomic.Int32
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { calls.Add(1); fmt.Fprint(w, "ok") }))
	defer s.Close()
	c := newTestClient(t, Config{MinInterval: time.Second})
	if _, err := get(t, c, s.URL); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Millisecond)
	defer cancel()
	_, err := c.Do(ctx, Request{URL: s.URL, ReadOnly: true})
	testError(t, err, "timeout")
	if calls.Load() != 1 {
		t.Fatalf("calls=%d", calls.Load())
	}
}

func TestCoalescingAndScopeIsolation(t *testing.T) {
	var calls atomic.Int32
	entered := make(chan struct{})
	release := make(chan struct{})
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if calls.Add(1) == 1 {
			close(entered)
			<-release
		}
		fmt.Fprint(w, r.Header.Get("Authorization"))
	}))
	defer s.Close()
	c := newTestClient(t, Config{})
	req := Request{URL: s.URL, ReadOnly: true, CacheTTL: time.Hour, CacheScope: "one", Headers: http.Header{"Authorization": {"account-one"}}}
	var wg sync.WaitGroup
	for range 12 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if _, err := c.Do(context.Background(), req); err != nil {
				t.Error(err)
			}
		}()
	}
	<-entered
	deadline := time.Now().Add(time.Second)
	for {
		c.mu.Lock()
		joined := 0
		for _, f := range c.flights {
			joined = f.waiters
		}
		c.mu.Unlock()
		if joined == 12 {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("read callers did not coalesce")
		}
		time.Sleep(time.Millisecond)
	}
	close(release)
	wg.Wait()
	if calls.Load() != 1 {
		t.Fatalf("coalesced calls=%d", calls.Load())
	}
	other := req
	other.Headers = http.Header{"Authorization": {"account-two"}}
	r, err := c.Do(context.Background(), other)
	if err != nil || string(r.Body) != "account-two" || calls.Load() != 2 {
		t.Fatalf("isolation: body=%s calls=%d err=%v", r.Body, calls.Load(), err)
	}
	other = req
	other.CacheScope = "two"
	if _, err := c.Do(context.Background(), other); err != nil {
		t.Fatal(err)
	}
	if calls.Load() != 3 {
		t.Fatalf("scope calls=%d", calls.Load())
	}
}

func TestWaiterCanCancelWithoutStoppingOther(t *testing.T) {
	entered, release := make(chan struct{}), make(chan struct{})
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { close(entered); <-release; fmt.Fprint(w, "ok") }))
	defer s.Close()
	c := newTestClient(t, Config{})
	req := Request{URL: s.URL, ReadOnly: true}
	first := make(chan error, 1)
	go func() { _, err := c.Do(context.Background(), req); first <- err }()
	<-entered
	ctx, cancel := context.WithCancel(context.Background())
	second := make(chan error, 1)
	go func() { _, err := c.Do(ctx, req); second <- err }()
	// Ensure the second caller has joined the flight before cancellation.
	deadline := time.Now().Add(time.Second)
	for {
		c.mu.Lock()
		n := 0
		for _, f := range c.flights {
			n = f.waiters
		}
		c.mu.Unlock()
		if n == 2 {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("second caller did not join")
		}
		time.Sleep(time.Millisecond)
	}
	cancel()
	testError(t, <-second, "canceled")
	close(release)
	if err := <-first; err != nil {
		t.Fatal(err)
	}
}

func TestInvalidateWinsOverInFlightCache(t *testing.T) {
	var calls atomic.Int32
	entered, release := make(chan struct{}), make(chan struct{})
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if calls.Add(1) == 1 {
			close(entered)
			<-release
			fmt.Fprint(w, "old")
			return
		}
		fmt.Fprint(w, "new")
	}))
	defer s.Close()
	c := newTestClient(t, Config{})
	req := Request{URL: s.URL, ReadOnly: true, CacheTTL: time.Hour, CacheScope: "account"}
	first := make(chan error, 1)
	go func() { _, err := c.Do(context.Background(), req); first <- err }()
	<-entered
	c.Invalidate("account")
	second, err := c.Do(context.Background(), req)
	if err != nil || string(second.Body) != "new" {
		t.Fatalf("second=%s err=%v", second.Body, err)
	}
	close(release)
	if err := <-first; err != nil {
		t.Fatal(err)
	}
	third, err := c.Do(context.Background(), req)
	if err != nil || string(third.Body) != "new" || calls.Load() != 2 {
		t.Fatalf("third=%s calls=%d err=%v", third.Body, calls.Load(), err)
	}
}

func TestBodyLimitAndRedirect(t *testing.T) {
	var targetCalls atomic.Int32
	target := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { targetCalls.Add(1) }))
	defer target.Close()
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/redirect" {
			w.Header().Set("Location", target.URL)
			w.WriteHeader(http.StatusFound)
			return
		}
		fmt.Fprint(w, "too-large")
	}))
	defer s.Close()
	c := newTestClient(t, Config{MaxBodyBytes: 4})
	_, err := get(t, c, s.URL)
	testError(t, err, "body_limit")
	_, err = get(t, c, s.URL+"/redirect")
	e := testError(t, err, "http")
	if e.Status != 302 || targetCalls.Load() != 0 {
		t.Fatalf("redirect status=%d target calls=%d", e.Status, targetCalls.Load())
	}
}

func TestCacheLRUAndNoErrorCaching(t *testing.T) {
	var calls atomic.Int32
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		n := calls.Add(1)
		if r.URL.Path == "/error" && n == 1 {
			w.WriteHeader(403)
			return
		}
		fmt.Fprint(w, "ok")
	}))
	defer s.Close()
	c := newTestClient(t, Config{CacheEntries: 1})
	req := func(path string) Request { return Request{URL: s.URL + path, ReadOnly: true, CacheTTL: time.Hour} }
	if _, err := c.Do(context.Background(), req("/error")); err == nil {
		t.Fatal("expected initial error")
	}
	if _, err := c.Do(context.Background(), req("/error")); err != nil {
		t.Fatal(err)
	}
	if _, err := c.Do(context.Background(), req("/error")); err != nil {
		t.Fatal(err)
	}
	if calls.Load() != 2 {
		t.Fatalf("error cached or success missed: calls=%d", calls.Load())
	}
	if _, err := c.Do(context.Background(), req("/other")); err != nil {
		t.Fatal(err)
	}
	if _, err := c.Do(context.Background(), req("/error")); err != nil {
		t.Fatal(err)
	}
	if calls.Load() != 4 {
		t.Fatalf("LRU eviction missed: calls=%d", calls.Load())
	}
}

func TestWriteNetworkFailureAmbiguous(t *testing.T) {
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hj := w.(http.Hijacker)
		conn, _, _ := hj.Hijack()
		conn.Close()
	}))
	defer s.Close()
	c := newTestClient(t, Config{MaxAttempts: 3})
	_, err := c.Do(context.Background(), Request{URL: s.URL, Method: http.MethodPost})
	if !testError(t, err, "network").Ambiguous {
		t.Fatal("write failure should be ambiguous")
	}
}

func TestGETWriteUsesFreshConnectionAndIsNotReplayed(t *testing.T) {
	var connections, writes atomic.Int32
	s := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/warm" {
			fmt.Fprint(w, "ok")
			return
		}
		writes.Add(1)
		conn, _, _ := w.(http.Hijacker).Hijack()
		conn.Close()
	}))
	s.Config.ConnState = func(_ net.Conn, state http.ConnState) {
		if state == http.StateNew {
			connections.Add(1)
		}
	}
	s.Start()
	defer s.Close()
	c := newTestClient(t, Config{})
	if _, err := get(t, c, s.URL+"/warm"); err != nil {
		t.Fatal(err)
	}
	_, err := c.Do(context.Background(), Request{URL: s.URL + "/publish", Method: http.MethodGet})
	if !testError(t, err, "network").Ambiguous || writes.Load() != 1 || connections.Load() != 2 {
		t.Fatalf("write must use one new connection: writes=%d connections=%d error=%v", writes.Load(), connections.Load(), err)
	}
}

func TestCanceledWaitersDoNotReserveFutureSlots(t *testing.T) {
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { fmt.Fprint(w, "ok") }))
	defer s.Close()
	c := newTestClient(t, Config{MinInterval: 100 * time.Millisecond})
	if _, err := get(t, c, s.URL); err != nil {
		t.Fatal(err)
	}
	for range 10 {
		ctx, cancel := context.WithTimeout(context.Background(), time.Millisecond)
		_, err := c.Do(ctx, Request{URL: s.URL, ReadOnly: true})
		cancel()
		if !errors.Is(err, context.DeadlineExceeded) {
			t.Fatalf("deadline: %v", err)
		}
	}
	ctx, cancel := context.WithTimeout(context.Background(), 300*time.Millisecond)
	defer cancel()
	if _, err := c.Do(ctx, Request{URL: s.URL, ReadOnly: true}); err != nil {
		t.Fatalf("cancelled calls consumed pacing slots: %v", err)
	}
}

func BenchmarkCachedRead(b *testing.B) {
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { fmt.Fprint(w, `{"id":"item"}`) }))
	defer s.Close()
	c, err := New(Config{})
	if err != nil {
		b.Fatal(err)
	}
	defer c.Close()
	req := Request{URL: s.URL, ReadOnly: true, CacheTTL: time.Hour, CacheScope: "benchmark"}
	if _, err := c.Do(context.Background(), req); err != nil {
		b.Fatal(err)
	}
	b.ReportAllocs()
	b.ResetTimer()
	for b.Loop() {
		if _, err := c.Do(context.Background(), req); err != nil {
			b.Fatal(err)
		}
	}
}
