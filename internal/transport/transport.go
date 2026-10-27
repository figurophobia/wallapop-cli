// Package transport provides bounded, paced HTTP access for Wallapop clients.
package transport

import (
	"bytes"
	"container/list"
	"context"
	"crypto/sha256"
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"math"
	"math/rand"
	"net"
	"net/http"
	"sort"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
)

const (
	defaultTimeout      = 20 * time.Second
	defaultMaxBodyBytes = 4 << 20
	defaultCacheEntries = 128
	maxCacheBytes       = 32 << 20
)

type Config struct {
	HTTPClient   *http.Client
	MinInterval  time.Duration
	Timeout      time.Duration
	MaxAttempts  int
	MaxBodyBytes int64
	CacheEntries int
}

type Request struct {
	Method     string
	URL        string
	Headers    http.Header
	Body       []byte
	ReadOnly   bool
	CacheTTL   time.Duration
	CacheScope string
}

type Response struct {
	Status int
	Header http.Header
	Body   []byte
}

// Error intentionally excludes URLs, request headers, response bodies, and tokens.
type Error struct {
	Kind       string
	Status     int
	RetryAfter time.Duration
	Ambiguous  bool
	cause      error
}

func (e *Error) Error() string {
	if e == nil {
		return "transport error"
	}
	message := "transport " + e.Kind
	if e.Status != 0 {
		message = fmt.Sprintf("%s (HTTP %d)", message, e.Status)
	}
	if e.Ambiguous {
		message += "; outcome unknown; check state before retrying"
	}
	return message
}

func (e *Error) Unwrap() error {
	if e == nil {
		return nil
	}
	return e.cause
}

type cacheEntry struct {
	key     [32]byte
	scope   string
	value   Response
	expires time.Time
	bytes   int64
}

type flightKey struct {
	key        [32]byte
	generation uint64
}

type flight struct {
	done    chan struct{}
	cancel  context.CancelFunc
	waiters int
	result  Response
	err     error
}

type Client struct {
	httpClient  *http.Client
	writeClient *http.Client
	interval    time.Duration
	timeout     time.Duration
	attempts    int
	maxBody     int64
	cacheLimit  int
	byteLimit   int64

	mu         sync.Mutex
	paceGate   chan struct{}
	closedCh   chan struct{}
	nextStart  time.Time
	closed     bool
	cache      map[[32]byte]*list.Element
	lru        list.List
	cacheBytes int64
	flights    map[flightKey]*flight
	generation map[string]uint64
}

func New(cfg Config) (*Client, error) {
	if cfg.MinInterval < 0 || cfg.Timeout < 0 || cfg.MaxAttempts < 0 || cfg.MaxBodyBytes < 0 || cfg.CacheEntries < 0 || cfg.MaxBodyBytes == math.MaxInt64 {
		return nil, &Error{Kind: "invalid_config"}
	}
	if cfg.Timeout == 0 {
		cfg.Timeout = defaultTimeout
	}
	if cfg.MaxAttempts == 0 {
		cfg.MaxAttempts = 3
	}
	if cfg.MaxBodyBytes == 0 {
		cfg.MaxBodyBytes = defaultMaxBodyBytes
	}
	if cfg.CacheEntries == 0 {
		cfg.CacheEntries = defaultCacheEntries
	}
	base := cfg.HTTPClient
	if base == nil {
		pooled := http.DefaultTransport.(*http.Transport).Clone()
		pooled.MaxResponseHeaderBytes = 64 << 10
		pooled.MaxIdleConnsPerHost = 4
		base = &http.Client{Transport: pooled}
	}
	hc := *base
	// A redirect may disclose auth headers or convert an API response into HTML.
	hc.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	wc := hc
	transport := hc.Transport
	if transport == nil {
		transport = http.DefaultTransport
	}
	if standard, ok := transport.(*http.Transport); ok {
		// A fresh connection per write prevents net/http from silently
		// replaying a write-shaped GET after a reused connection fails.
		// Negotiation stays untouched: forcing HTTP/1 breaks against servers
		// that answer with HTTP/2 frames regardless of the ALPN offer.
		oneShot := standard.Clone()
		oneShot.DisableKeepAlives = true
		wc.Transport = oneShot
	}
	limit := int64(maxCacheBytes)
	if cfg.MaxBodyBytes <= maxCacheBytes/int64(cfg.CacheEntries) {
		limit = cfg.MaxBodyBytes * int64(cfg.CacheEntries)
	}
	gate := make(chan struct{}, 1)
	gate <- struct{}{}
	return &Client{httpClient: &hc, writeClient: &wc, paceGate: gate, closedCh: make(chan struct{}), interval: cfg.MinInterval, timeout: cfg.Timeout,
		attempts: cfg.MaxAttempts, maxBody: cfg.MaxBodyBytes, cacheLimit: cfg.CacheEntries,
		byteLimit: limit, cache: make(map[[32]byte]*list.Element),
		flights: make(map[flightKey]*flight), generation: make(map[string]uint64)}, nil
}

func (c *Client) Do(ctx context.Context, req Request) (Response, error) {
	if ctx == nil {
		return Response{}, &Error{Kind: "invalid_request"}
	}
	if req.Method == "" {
		req.Method = http.MethodGet
	}
	if !validRequest(req) {
		return Response{}, &Error{Kind: "invalid_request"}
	}
	if err := ctx.Err(); err != nil {
		return Response{}, contextError(err, false)
	}
	key := requestKey(req)
	if !req.ReadOnly {
		callCtx, cancel := context.WithTimeout(ctx, c.timeout)
		defer cancel()
		return c.perform(callCtx, req)
	}
	c.mu.Lock()
	if c.closed {
		c.mu.Unlock()
		return Response{}, &Error{Kind: "closed"}
	}
	if req.CacheTTL > 0 {
		if elem := c.cache[key]; elem != nil {
			entry := elem.Value.(*cacheEntry)
			if time.Now().Before(entry.expires) {
				c.lru.MoveToFront(elem)
				value := cloneResponse(entry.value)
				c.mu.Unlock()
				return value, nil
			}
			c.removeCache(elem)
		}
	}
	fk := flightKey{key: key, generation: c.generation[req.CacheScope]}
	if f := c.flights[fk]; f != nil {
		f.waiters++
		c.mu.Unlock()
		return c.wait(ctx, fk, f)
	}
	callCtx, cancel := context.WithTimeout(context.Background(), c.timeout)
	f := &flight{done: make(chan struct{}), cancel: cancel, waiters: 1}
	c.flights[fk] = f
	c.mu.Unlock()
	go c.runFlight(callCtx, fk, req, f)
	return c.wait(ctx, fk, f)
}

func validRequest(req Request) bool {
	if req.CacheTTL < 0 || strings.ContainsAny(req.Method, " \r\n\t") {
		return false
	}
	u, err := http.NewRequest(req.Method, req.URL, nil)
	return err == nil && u.URL != nil && (u.URL.Scheme == "http" || u.URL.Scheme == "https") && u.URL.Host != ""
}

func (c *Client) wait(ctx context.Context, key flightKey, f *flight) (Response, error) {
	select {
	case <-f.done:
		return cloneResponse(f.result), f.err
	case <-ctx.Done():
		c.mu.Lock()
		f.waiters--
		if f.waiters == 0 {
			if c.flights[key] == f {
				delete(c.flights, key)
			}
			f.cancel()
		}
		c.mu.Unlock()
		return Response{}, contextError(ctx.Err(), false)
	}
}

func (c *Client) runFlight(ctx context.Context, key flightKey, req Request, f *flight) {
	result, err := c.perform(ctx, req)
	c.mu.Lock()
	if c.flights[key] == f {
		delete(c.flights, key)
	}
	if err == nil && req.CacheTTL > 0 && !c.closed && c.generation[req.CacheScope] == key.generation {
		c.putCache(key.key, req.CacheScope, result, req.CacheTTL)
	}
	f.result, f.err = result, err
	close(f.done)
	c.mu.Unlock()
	f.cancel()
}

func (c *Client) perform(ctx context.Context, req Request) (Response, error) {
	for attempt := 1; attempt <= c.attempts; attempt++ {
		if err := c.pace(ctx); err != nil {
			var typed *Error
			if errors.As(err, &typed) {
				return Response{}, typed
			}
			return Response{}, contextError(err, false)
		}
		httpReq, err := http.NewRequestWithContext(ctx, req.Method, req.URL, bytes.NewReader(req.Body))
		if err != nil {
			return Response{}, &Error{Kind: "invalid_request"}
		}
		httpReq.Header = req.Headers.Clone()
		client := c.httpClient
		if !req.ReadOnly {
			// Also disable body replay for transports that inspect GetBody.
			httpReq.GetBody = nil
			client = c.writeClient
		}
		resp, err := client.Do(httpReq)
		if err != nil {
			if ctx.Err() != nil {
				return Response{}, contextError(ctx.Err(), !req.ReadOnly)
			}
			if req.ReadOnly && attempt < c.attempts && transient(err) {
				if err := sleep(ctx, backoff(attempt)); err != nil {
					return Response{}, contextError(err, false)
				}
				continue
			}
			return Response{}, &Error{Kind: "network", Ambiguous: !req.ReadOnly}
		}
		body, readErr := io.ReadAll(io.LimitReader(resp.Body, c.maxBody+1))
		closeErr := resp.Body.Close()
		result := Response{Status: resp.StatusCode, Header: resp.Header.Clone(), Body: body}
		if int64(len(body)) > c.maxBody {
			return Response{Status: result.Status, Header: result.Header}, &Error{Kind: "body_limit", Status: resp.StatusCode, Ambiguous: !req.ReadOnly}
		}
		if readErr != nil || closeErr != nil {
			if ctx.Err() != nil {
				return Response{}, contextError(ctx.Err(), !req.ReadOnly)
			}
			if req.ReadOnly && attempt < c.attempts && transient(readErr) {
				if err := sleep(ctx, backoff(attempt)); err != nil {
					return Response{}, contextError(err, false)
				}
				continue
			}
			return Response{Status: result.Status, Header: result.Header}, &Error{Kind: "network", Status: resp.StatusCode, Ambiguous: !req.ReadOnly}
		}
		if resp.StatusCode >= 200 && resp.StatusCode < 300 {
			return result, nil
		}
		retryAfter := parseRetryAfter(resp.Header.Get("Retry-After"))
		if req.ReadOnly && attempt < c.attempts && retryableStatus(resp.StatusCode) {
			delay := backoff(attempt)
			if retryAfter > delay {
				delay = retryAfter
			}
			if err := sleep(ctx, delay); err != nil {
				return Response{}, contextError(err, false)
			}
			continue
		}
		return result, &Error{Kind: "http", Status: resp.StatusCode, RetryAfter: retryAfter,
			Ambiguous: !req.ReadOnly && resp.StatusCode >= 500}
	}
	return Response{}, &Error{Kind: "retry_exhausted"}
}

func (c *Client) pace(ctx context.Context) error {
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-c.closedCh:
		return &Error{Kind: "closed"}
	case <-c.paceGate:
	}
	defer func() { c.paceGate <- struct{}{} }()
	if err := ctx.Err(); err != nil {
		return err
	}
	select {
	case <-c.closedCh:
		return &Error{Kind: "closed"}
	default:
	}
	if err := sleepOrClose(ctx, c.closedCh, time.Until(c.nextStart)); err != nil {
		return err
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	select {
	case <-c.closedCh:
		return &Error{Kind: "closed"}
	default:
	}
	c.nextStart = time.Now().Add(c.interval)
	return nil
}

func (c *Client) Invalidate(scope string) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.generation[scope]++
	for elem := c.lru.Back(); elem != nil; {
		prev := elem.Prev()
		if elem.Value.(*cacheEntry).scope == scope {
			c.removeCache(elem)
		}
		elem = prev
	}
}

func (c *Client) Close() {
	c.mu.Lock()
	if c.closed {
		c.mu.Unlock()
		return
	}
	c.closed = true
	close(c.closedCh)
	for _, f := range c.flights {
		f.cancel()
	}
	c.flights = make(map[flightKey]*flight)
	c.cache = make(map[[32]byte]*list.Element)
	c.lru.Init()
	c.cacheBytes = 0
	c.mu.Unlock()
	c.httpClient.CloseIdleConnections()
	if c.writeClient.Transport != c.httpClient.Transport {
		c.writeClient.CloseIdleConnections()
	}
}

func (c *Client) putCache(key [32]byte, scope string, value Response, ttl time.Duration) {
	bytes := int64(len(value.Body))
	for key, values := range value.Header {
		bytes += int64(len(key))
		for _, v := range values {
			bytes += int64(len(v))
		}
	}
	if bytes > c.byteLimit {
		return
	}
	if elem := c.cache[key]; elem != nil {
		c.removeCache(elem)
	}
	entry := &cacheEntry{key: key, scope: scope, value: cloneResponse(value), expires: time.Now().Add(ttl), bytes: bytes}
	c.cache[key] = c.lru.PushFront(entry)
	c.cacheBytes += bytes
	for len(c.cache) > c.cacheLimit || c.cacheBytes > c.byteLimit {
		c.removeCache(c.lru.Back())
	}
}

func (c *Client) removeCache(elem *list.Element) {
	entry := elem.Value.(*cacheEntry)
	delete(c.cache, entry.key)
	c.cacheBytes -= entry.bytes
	c.lru.Remove(elem)
}

func cloneResponse(r Response) Response {
	r.Header = r.Header.Clone()
	r.Body = append([]byte(nil), r.Body...)
	return r
}

func requestKey(r Request) [32]byte {
	h := sha256.New()
	write := func(s string) {
		var b [8]byte
		binary.LittleEndian.PutUint64(b[:], uint64(len(s)))
		h.Write(b[:])
		h.Write([]byte(s))
	}
	write(r.Method)
	write(r.URL)
	write(r.CacheScope)
	write(string(r.Body))
	canonical := make(map[string][]string, len(r.Headers))
	for k, values := range r.Headers {
		canonical[http.CanonicalHeaderKey(k)] = append(canonical[http.CanonicalHeaderKey(k)], values...)
	}
	keys := make([]string, 0, len(canonical))
	for k := range canonical {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, k := range keys {
		write(k)
		values := append([]string(nil), canonical[k]...)
		sort.Strings(values)
		for _, v := range values {
			write(v)
		}
	}
	var key [32]byte
	copy(key[:], h.Sum(nil))
	return key
}

func retryableStatus(status int) bool {
	return status == 429 || status == 502 || status == 503 || status == 504
}

func parseRetryAfter(value string) time.Duration {
	value = strings.TrimSpace(value)
	if value == "" {
		return 0
	}
	if value[0] == '-' {
		return 0
	}
	if n, err := strconv.ParseUint(value, 10, 64); err == nil {
		if n == 0 {
			return 0
		}
		if n > uint64(math.MaxInt64/int64(time.Second)) {
			return time.Duration(math.MaxInt64)
		}
		return time.Duration(n) * time.Second
	} else if len(value) > 0 && value[0] >= '0' && value[0] <= '9' && !strings.ContainsAny(value, " ,:") {
		// Numeric values beyond uint64 still mean a distant retry, never now.
		return time.Duration(math.MaxInt64)
	}
	if t, err := http.ParseTime(value); err == nil {
		d := time.Until(t)
		if d < 0 {
			return 0
		}
		return d
	}
	return 0
}

func transient(err error) bool {
	if err == nil || errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
		return false
	}
	if errors.Is(err, io.EOF) || errors.Is(err, io.ErrUnexpectedEOF) || errors.Is(err, syscall.ECONNRESET) || errors.Is(err, syscall.ECONNREFUSED) || errors.Is(err, syscall.EPIPE) || errors.Is(err, syscall.ETIMEDOUT) {
		return true
	}
	var nerr net.Error
	return errors.As(err, &nerr) && (nerr.Timeout() || nerr.Temporary())
}

func backoff(attempt int) time.Duration {
	base := 200 * time.Millisecond
	for i := 1; i < attempt && base < 5*time.Second; i++ {
		base *= 2
	}
	if base > 5*time.Second {
		base = 5 * time.Second
	}
	return base/2 + time.Duration(rand.Int63n(int64(base)))
}

func sleep(ctx context.Context, d time.Duration) error {
	if d <= 0 {
		return ctx.Err()
	}
	timer := time.NewTimer(d)
	defer timer.Stop()
	select {
	case <-timer.C:
		return ctx.Err()
	case <-ctx.Done():
		return ctx.Err()
	}
}

func sleepOrClose(ctx context.Context, closed <-chan struct{}, d time.Duration) error {
	if d <= 0 {
		return ctx.Err()
	}
	timer := time.NewTimer(d)
	defer timer.Stop()
	select {
	case <-timer.C:
		return ctx.Err()
	case <-ctx.Done():
		return ctx.Err()
	case <-closed:
		return &Error{Kind: "closed"}
	}
}

func contextError(err error, ambiguous bool) *Error {
	if errors.Is(err, context.DeadlineExceeded) {
		return &Error{Kind: "timeout", Ambiguous: ambiguous, cause: context.DeadlineExceeded}
	}
	return &Error{Kind: "canceled", Ambiguous: ambiguous, cause: context.Canceled}
}
