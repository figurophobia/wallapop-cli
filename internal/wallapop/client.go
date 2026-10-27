// Package wallapop implements a bounded, cache-aware Wallapop API client.
//
// The package exposes a single dispatch surface (Invoke) plus per-operation
// helpers. Every network call goes through internal/transport, which enforces
// pacing, body limits, retries for read-only requests and a scoped response
// cache. Reads are cached per scope (one scope per client/account) and every
// write invalidates that scope before and after the mutation, so a stale
// cached body can never be served after a change.
package wallapop

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/jrrome/wallapop-mcp/internal/transport"
)

// Cache lifetimes used for the read operations.
const (
	cacheTTLPublic   = 30 * time.Second // search, item, seller
	cacheTTLMessage  = 5 * time.Second  // inbox, conversation, unread, offers, transactions
	cacheTTLCategory = time.Hour        // category tree
)

const (
	sessionTokenCookie = "__Secure-next-auth.session-token"
	deviceIDCookie     = "device_id"
	deviceFGPCookie    = "device_fgp"
	accessTokenCookie  = "accessToken"
)

var clientSeq atomic.Uint64

// Config configures a Client.
type Config struct {
	Transport                                                            *transport.Client
	APIBase, WebBase, PubNubBase, AccessToken, RefreshToken, SessionPath string
	Cookies                                                              map[string]string
	EnableWrites                                                         bool
}

// APIError is the error type returned by every operation. It never carries
// URLs, tokens or response bodies.
type APIError struct {
	Operation string
	Kind      string
	Status    int
	// Ambiguous marks a failure whose outcome is unknown: the request may or
	// may not have been applied upstream. Callers must check the state before
	// retrying.
	Ambiguous bool
	// Details carries non-sensitive context (e.g. the conversation id).
	Details map[string]any
	// Err is the wrapped cause, if any (context errors are propagated so
	// errors.Is(err, context.Canceled) keeps working).
	Err error
}

func (e *APIError) Error() string {
	if e == nil {
		return "wallapop error"
	}
	msg := e.Operation + ": " + e.Kind
	if e.Status > 0 {
		msg = fmt.Sprintf("%s (HTTP %d)", msg, e.Status)
	}
	if e.Ambiguous {
		msg += "; outcome unknown, check state before retrying"
	}
	return msg
}

// Unwrap exposes the wrapped cause (context errors included).
func (e *APIError) Unwrap() error { return e.Err }

// OutcomeUnknown reports that an attempted mutation may have applied upstream.
func (e *APIError) OutcomeUnknown() bool { return e != nil && e.Ambiguous }

// Client is a Wallapop API client bound to one account.
type Client struct {
	tr            *transport.Client
	api, web, pub string
	writes        bool
	scope         string

	// tokenMu guards the credential material. It is never held across a
	// network call.
	tokenMu sync.Mutex
	access  string
	refresh string
	cookies map[string]string

	sessionPath string

	// refreshMu + refreshCall implement a cancellable single-flight gate for
	// token refreshes.
	refreshMu   sync.Mutex
	refreshCall *refreshCall
}

type refreshCall struct {
	done chan struct{}
	err  error
}

// New builds a client. When SessionPath is set the session/credential files
// are loaded (and merged) from disk.
func New(cfg Config) (*Client, error) {
	if cfg.Transport == nil {
		return nil, errors.New("transport is required")
	}
	c := &Client{
		tr:          cfg.Transport,
		api:         strings.TrimRight(cfg.APIBase, "/"),
		web:         strings.TrimRight(cfg.WebBase, "/"),
		pub:         strings.TrimRight(cfg.PubNubBase, "/"),
		writes:      cfg.EnableWrites,
		access:      cfg.AccessToken,
		refresh:     cfg.RefreshToken,
		cookies:     map[string]string{},
		sessionPath: cfg.SessionPath,
	}
	if c.api == "" {
		c.api = "https://api.wallapop.com"
	}
	if c.web == "" {
		c.web = "https://es.wallapop.com"
	}
	if c.pub == "" {
		c.pub = "https://ps7.pndsn.com"
	}
	for k, v := range cfg.Cookies {
		if v != "" {
			c.cookies[k] = v
		}
	}
	if cfg.SessionPath != "" {
		if err := c.loadSession(); err != nil {
			return nil, err
		}
	}
	c.scope = c.buildScope()
	return c, nil
}

// buildScope derives a cache scope that isolates this client (and therefore
// this account) from every other client sharing the same transport.
func (c *Client) buildScope() string {
	h := sha256.New()
	c.tokenMu.Lock()
	h.Write([]byte(c.access))
	h.Write([]byte{0})
	h.Write([]byte(c.refresh))
	h.Write([]byte{0})
	for _, k := range []string{sessionTokenCookie, deviceIDCookie, deviceFGPCookie, accessTokenCookie} {
		h.Write([]byte(c.cookies[k]))
		h.Write([]byte{0})
	}
	c.tokenMu.Unlock()
	account := hex.EncodeToString(h.Sum(nil))[:12]
	return fmt.Sprintf("wallapop/%d/%s", clientSeq.Add(1), account)
}

// --- session handling -------------------------------------------------------

// loadSession reads the session (and the legacy credentials file) from disk.
// A directory path is normalized to <dir>/session.json before any early
// return, so a later save writes to the canonical location.
func (c *Client) loadSession() error {
	path := c.sessionPath
	if path == "" {
		return nil
	}
	st, err := os.Stat(path)
	switch {
	case err == nil && st.IsDir():
		dir := path
		c.sessionPath = filepath.Join(dir, "session.json")
		if e := c.mergeSessionFile(c.sessionPath); e != nil {
			return e
		}
		return c.mergeSessionFile(filepath.Join(dir, "credentials.json"))
	case err == nil:
		if !st.Mode().IsRegular() {
			return fmt.Errorf("session path %s is not a regular file", filepath.Base(path))
		}
		if e := c.mergeSessionFile(path); e != nil {
			return e
		}
		if filepath.Base(path) == "session.json" {
			return c.mergeSessionFile(filepath.Join(filepath.Dir(path), "credentials.json"))
		}
		return nil
	case os.IsNotExist(err):
		// Keep the path so a later save creates the file.
		if filepath.Base(path) == "session.json" {
			return c.mergeSessionFile(filepath.Join(filepath.Dir(path), "credentials.json"))
		}
		return nil
	default:
		return err
	}
}

// mergeSessionFile merges one session/credential file into the client. Values
// already known (explicit configuration or a previously merged file) win.
func (c *Client) mergeSessionFile(path string) error {
	b, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return nil
		}
		return err
	}
	var m map[string]json.RawMessage
	if err := json.Unmarshal(b, &m); err != nil || m == nil {
		return fmt.Errorf("invalid session JSON in %s", filepath.Base(path))
	}
	c.tokenMu.Lock()
	defer c.tokenMu.Unlock()
	if c.access == "" {
		c.access = sessionString(m, "access_token", "accessToken", "token")
	}
	if c.access == "" {
		if d, ok := sessionObject(m, "data"); ok {
			c.access = sessionString(d, "access_token", "accessToken", "token")
		}
	}
	if c.refresh == "" {
		c.refresh = sessionString(m, "refresh_token", "refreshToken")
	}
	if c.refresh == "" {
		if d, ok := sessionObject(m, "data"); ok {
			c.refresh = sessionString(d, "refresh_token", "refreshToken")
		}
	}
	if raw, ok := m["cookies"]; ok {
		var cm map[string]string
		if json.Unmarshal(raw, &cm) == nil {
			for k, v := range cm {
				if v != "" && c.cookies[k] == "" {
					c.cookies[k] = v
				}
			}
		}
	}
	for _, k := range []string{sessionTokenCookie, deviceIDCookie, deviceFGPCookie, accessTokenCookie} {
		if v := sessionString(m, k); v != "" && c.cookies[k] == "" {
			c.cookies[k] = v
		}
	}
	return nil
}

func sessionString(m map[string]json.RawMessage, keys ...string) string {
	for _, k := range keys {
		raw, ok := m[k]
		if !ok {
			continue
		}
		var s string
		if json.Unmarshal(raw, &s) == nil && s != "" {
			return s
		}
	}
	return ""
}

func sessionObject(m map[string]json.RawMessage, key string) (map[string]json.RawMessage, bool) {
	raw, ok := m[key]
	if !ok {
		return nil, false
	}
	var out map[string]json.RawMessage
	if json.Unmarshal(raw, &out) != nil || out == nil {
		return nil, false
	}
	return out, true
}

// saveSession persists the session atomically with 0600 permissions. Unknown
// fields of the existing file are retained and the access token is
// harmonized across access_token/accessToken/token and the cookies object.
func (c *Client) saveSession() error {
	if c.sessionPath == "" {
		return nil
	}
	dir := filepath.Dir(c.sessionPath)
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return err
	}
	existing := map[string]any{}
	if b, err := os.ReadFile(c.sessionPath); err == nil {
		var m map[string]any
		if json.Unmarshal(b, &m) == nil && m != nil {
			existing = m
		}
	}
	cookies := map[string]any{}
	if cm, ok := existing["cookies"].(map[string]any); ok {
		for k, v := range cm {
			cookies[k] = v
		}
	}
	c.tokenMu.Lock()
	for k, v := range c.cookies {
		if v != "" {
			cookies[k] = v
		}
	}
	if c.access != "" {
		cookies[accessTokenCookie] = c.access
		c.cookies[accessTokenCookie] = c.access
	}
	access, refresh := c.access, c.refresh
	c.tokenMu.Unlock()

	existing["cookies"] = cookies
	existing["access_token"] = access
	existing["accessToken"] = access
	existing["token"] = access
	if refresh != "" {
		existing["refresh_token"] = refresh
		existing["refreshToken"] = refresh
	}
	c.tokenMu.Lock()
	for k, v := range c.cookies {
		if v != "" && k != accessTokenCookie {
			existing[k] = v
		}
	}
	existing[accessTokenCookie] = c.access
	c.tokenMu.Unlock()

	b, err := json.Marshal(existing)
	if err != nil {
		return err
	}
	f, err := os.CreateTemp(dir, ".session-*")
	if err != nil {
		return err
	}
	tmp := f.Name()
	defer os.Remove(tmp)
	if err = f.Chmod(0o600); err != nil {
		f.Close()
		return err
	}
	if _, err = f.Write(b); err != nil {
		f.Close()
		return err
	}
	if err = f.Sync(); err != nil {
		f.Close()
		return err
	}
	if err = f.Close(); err != nil {
		return err
	}
	return os.Rename(tmp, c.sessionPath)
}

// Status reports the session state. It never exposes the tokens themselves.
func (c *Client) Status() map[string]any {
	c.tokenMu.Lock()
	access, path := c.access, c.sessionPath
	c.tokenMu.Unlock()
	source := "none"
	switch {
	case access != "" && path != "":
		source = "session"
	case access != "":
		source = "token"
	}
	out := map[string]any{
		"logged_in":          access != "",
		"credential_present": access != "",
		"session_verified":   false,
		"source":             source,
		"expiry":             nil,
		"writes_enabled":     c.writes,
	}
	if exp, ok := tokenExpiry(access); ok {
		out["expiry"] = exp.UTC().Format(time.RFC3339)
	}
	return out
}

func (c *Client) requireWrite(op string) error {
	if !c.writes {
		return &APIError{Operation: op, Kind: "writes_disabled"}
	}
	return nil
}

// --- request plumbing -------------------------------------------------------

type reqSpec struct {
	method      string
	path        string
	query       url.Values
	body        any
	contentType string
	auth        bool
	bff         bool
	extra       http.Header
	readOnly    bool
	ttl         time.Duration
	// rawURL bypasses the API base and the /api/v3 prefix. It is only used
	// for the PubNub publish endpoint, which lives on another host.
	rawURL string
}

func (c *Client) url(s reqSpec) string {
	if s.rawURL != "" {
		return s.rawURL
	}
	path := s.path
	if !s.bff {
		path = "/api/v3" + path
	}
	u := c.api + path
	if len(s.query) > 0 {
		u += "?" + s.query.Encode()
	}
	return u
}

func (c *Client) buildHeaders(s reqSpec) http.Header {
	h := http.Header{}
	h.Set("Accept", "application/json, text/plain, */*")
	h.Set("Origin", c.web)
	h.Set("Referer", c.web+"/")
	h.Set("X-DeviceOS", "0")
	h.Set("X-Product", "wallapop")
	h.Set("X-AppVersion", "826560")
	h.Set("Accept-Language", "es-ES,es;q=0.9,en;q=0.8")
	c.tokenMu.Lock()
	if v := c.cookies[deviceIDCookie]; v != "" {
		h.Set("X-DeviceID", v)
	}
	if s.auth && c.access != "" {
		h.Set("Authorization", "Bearer "+c.access)
	}
	c.tokenMu.Unlock()
	switch {
	case s.contentType != "":
		h.Set("Content-Type", s.contentType)
	case s.body != nil:
		h.Set("Content-Type", "application/json")
	}
	for k, v := range s.extra {
		h[k] = append([]string{}, v...)
	}
	return h
}

// do performs one API call. Read-only calls are cached according to the
// request TTL (a TTL of zero bypasses the cache entirely, which is what the
// `fresh` flag does).
func (c *Client) do(ctx context.Context, op string, s reqSpec) (any, error) {
	body, err := encodeBody(s.body)
	if err != nil {
		return nil, &APIError{Operation: op, Kind: "invalid_request", Err: err}
	}
	u := c.url(s)
	h := c.buildHeaders(s)
	old := strings.TrimPrefix(h.Get("Authorization"), "Bearer ")
	ttl := time.Duration(0)
	if s.readOnly {
		ttl = s.ttl
	}
	req := transport.Request{Method: s.method, URL: u, Headers: h, Body: body, ReadOnly: s.readOnly, CacheTTL: ttl, CacheScope: c.scope}
	resp, terr := c.tr.Do(ctx, req)
	if s.auth && is401(resp, terr) {
		if e := c.refreshAccess(ctx, old); e == nil {
			// Never repeat the call with the very same token.
			if fresh := c.currentAccess(); fresh != "" && fresh != old {
				req.Headers = c.buildHeaders(s)
				resp, terr = c.tr.Do(ctx, req)
			}
		} else {
			return nil, e
		}
	}
	if terr != nil {
		return nil, c.mapError(ctx, op, terr)
	}
	if resp.Status < 200 || resp.Status >= 300 {
		return nil, &APIError{Operation: op, Kind: "http_error", Status: resp.Status}
	}
	v, err := decodeBody(op, resp)
	if err != nil && !s.readOnly {
		var ae *APIError
		if errors.As(err, &ae) {
			ae.Ambiguous = true
		}
	}
	return v, err
}

// doRaw performs a request and returns the raw response (used for the
// PubNub publish acknowledgement).
func (c *Client) doRaw(ctx context.Context, op string, s reqSpec) (transport.Response, error) {
	body, err := encodeBody(s.body)
	if err != nil {
		return transport.Response{}, &APIError{Operation: op, Kind: "invalid_request", Err: err}
	}
	resp, terr := c.tr.Do(ctx, transport.Request{Method: s.method, URL: c.url(s), Headers: c.buildHeaders(s), Body: body, ReadOnly: s.readOnly, CacheScope: c.scope})
	if terr != nil {
		return resp, c.mapError(ctx, op, terr)
	}
	if resp.Status < 200 || resp.Status >= 300 {
		return resp, &APIError{Operation: op, Kind: "http_error", Status: resp.Status}
	}
	return resp, nil
}

func encodeBody(body any) ([]byte, error) {
	switch v := body.(type) {
	case nil:
		return nil, nil
	case []byte:
		return v, nil
	default:
		return json.Marshal(body)
	}
}

// decodeBody decodes a JSON body strictly: a single value, no trailing data,
// never a JSON null.
func decodeBody(op string, resp transport.Response) (any, error) {
	if len(bytes.TrimSpace(resp.Body)) == 0 {
		return map[string]any{"ok": true, "status": resp.Status}, nil
	}
	dec := json.NewDecoder(bytes.NewReader(resp.Body))
	dec.UseNumber()
	var out any
	if err := dec.Decode(&out); err != nil {
		return nil, &APIError{Operation: op, Kind: "invalid_json", Status: resp.Status, Err: err}
	}
	if err := dec.Decode(new(any)); err != io.EOF {
		return nil, &APIError{Operation: op, Kind: "invalid_json", Status: resp.Status}
	}
	if out == nil {
		return nil, &APIError{Operation: op, Kind: "invalid_response", Status: resp.Status}
	}
	return out, nil
}

func is401(r transport.Response, e error) bool {
	if r.Status == 401 {
		return true
	}
	var te *transport.Error
	return errors.As(e, &te) && te.Status == 401
}

// mapError converts a transport error into an APIError, preserving the
// ambiguous flag and propagating context errors.
func (c *Client) mapError(ctx context.Context, op string, err error) error {
	if ctxErr := ctx.Err(); ctxErr != nil {
		kind := "canceled"
		if errors.Is(ctxErr, context.DeadlineExceeded) {
			kind = "timeout"
		}
		return &APIError{Operation: op, Kind: kind, Ambiguous: ambiguous(err), Err: ctxErr}
	}
	var te *transport.Error
	if errors.As(err, &te) {
		return &APIError{Operation: op, Kind: te.Kind, Status: te.Status, Ambiguous: te.Ambiguous, Err: err}
	}
	return &APIError{Operation: op, Kind: "transport_error", Err: err}
}

func ambiguous(err error) bool {
	var te *transport.Error
	if errors.As(err, &te) {
		return te.Ambiguous
	}
	return false
}

// --- token refresh -----------------------------------------------------------

// currentAccess returns the access token currently in use.
func (c *Client) currentAccess() string {
	c.tokenMu.Lock()
	defer c.tokenMu.Unlock()
	return c.access
}

// isCanceled reports whether the error is a cancellation or a deadline.
func isCanceled(err error) bool {
	if err == nil {
		return false
	}
	var ae *APIError
	if errors.As(err, &ae) {
		return ae.Kind == "canceled" || ae.Kind == "timeout"
	}
	return errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded)
}

// refreshAccess refreshes the access token for oldToken. The token mutex is
// never held across the network call and concurrent callers share a single
// refresh (single flight) that can be cancelled by the caller.
func (c *Client) refreshAccess(ctx context.Context, oldToken string) error {
	c.tokenMu.Lock()
	current := c.access
	c.tokenMu.Unlock()
	if current != "" && current != oldToken {
		// Somebody else already refreshed the token.
		return nil
	}
	c.refreshMu.Lock()
	// A late 401 may arrive after the earlier refresh completed.
	c.tokenMu.Lock()
	current = c.access
	c.tokenMu.Unlock()
	if current != "" && current != oldToken {
		c.refreshMu.Unlock()
		return nil
	}
	if call := c.refreshCall; call != nil {
		c.refreshMu.Unlock()
		select {
		case <-call.done:
			if current := c.currentAccess(); current != "" && current != oldToken {
				return nil
			}
			return call.err
		case <-ctx.Done():
			return c.mapError(ctx, "session", ctx.Err())
		}
	}
	call := &refreshCall{done: make(chan struct{})}
	c.refreshCall = call
	c.refreshMu.Unlock()

	err := c.doRefresh(ctx, oldToken)

	c.refreshMu.Lock()
	call.err = err
	close(call.done)
	c.refreshCall = nil
	c.refreshMu.Unlock()
	return err
}

func (c *Client) doRefresh(ctx context.Context, oldToken string) error {
	c.tokenMu.Lock()
	refreshToken := c.refresh
	cookie := c.cookies[sessionTokenCookie]
	c.tokenMu.Unlock()

	var apiErr error
	if refreshToken != "" {
		token, newRefresh, err := c.apiRefresh(ctx, refreshToken)
		if err == nil && token != oldToken {
			return c.applyToken(token, newRefresh)
		}
		if err == nil {
			err = &APIError{Operation: "session", Kind: "refresh_same_token"}
		}
		apiErr = err
	}
	if cookie == "" {
		if apiErr != nil {
			// A cancelled attempt must not disable the refresh for good.
			return apiErr
		}
		return &APIError{Operation: "session", Kind: "refresh_unavailable"}
	}
	token, err := c.webRefresh(ctx, cookie)
	if err != nil {
		return err
	}
	if token == oldToken {
		return &APIError{Operation: "session", Kind: "refresh_same_token"}
	}
	return c.applyToken(token, "")
}

// apiRefresh exchanges the refresh token for a new access token.
func (c *Client) apiRefresh(ctx context.Context, refreshToken string) (string, string, error) {
	body, _ := json.Marshal(map[string]string{"refresh_token": refreshToken, "client_id": "wallapop"})
	h := http.Header{}
	h.Set("Accept", "application/json")
	h.Set("Content-Type", "application/json")
	resp, terr := c.tr.Do(ctx, transport.Request{Method: "POST", URL: c.api + "/api/v3/access/refresh", Headers: h, Body: body, ReadOnly: false, CacheScope: c.scope})
	if terr != nil {
		return "", "", c.refreshError(ctx, terr)
	}
	if resp.Status < 200 || resp.Status >= 300 {
		return "", "", &APIError{Operation: "session", Kind: "refresh_failed", Status: resp.Status}
	}
	access, refresh := extractTokenPayload(resp.Body)
	if access == "" {
		return "", "", &APIError{Operation: "session", Kind: "refresh_failed", Status: resp.Status}
	}
	return access, refresh, nil
}

// webRefresh exchanges the browser session cookie for a fresh access token.
func (c *Client) webRefresh(ctx context.Context, cookie string) (string, error) {
	c.tokenMu.Lock()
	parts := []string{sessionTokenCookie + "=" + cookie}
	for _, k := range []string{deviceIDCookie, deviceFGPCookie, accessTokenCookie} {
		if v := c.cookies[k]; v != "" {
			parts = append(parts, k+"="+v)
		}
	}
	c.tokenMu.Unlock()
	h := http.Header{}
	h.Set("Accept", "application/json")
	h.Set("Cookie", strings.Join(parts, "; "))
	h.Set("Accept-Language", "es-ES,es;q=0.9,en;q=0.8")
	resp, terr := c.tr.Do(ctx, transport.Request{Method: "GET", URL: c.web + "/api/auth/session", Headers: h, ReadOnly: false, CacheScope: c.scope})
	if terr != nil {
		return "", c.refreshError(ctx, terr)
	}
	if resp.Status < 200 || resp.Status >= 300 {
		return "", &APIError{Operation: "session", Kind: "refresh_failed", Status: resp.Status}
	}
	token, _ := extractTokenPayload(resp.Body)
	if token == "" {
		return "", &APIError{Operation: "session", Kind: "refresh_failed", Status: resp.Status}
	}
	return token, nil
}

// refreshError maps a transport failure of a refresh attempt. The requested
// account operation was not performed by a failed refresh attempt.
func (c *Client) refreshError(ctx context.Context, err error) error {
	mapped := c.mapError(ctx, "session", err)
	var ae *APIError
	if errors.As(mapped, &ae) {
		clone := *ae
		clone.Ambiguous = false
		return &clone
	}
	return mapped
}

// extractTokenPayload reads the access/refresh tokens from a flat or nested
// refresh response.
func extractTokenPayload(body []byte) (access, refresh string) {
	dec := json.NewDecoder(bytes.NewReader(body))
	dec.UseNumber()
	var m map[string]any
	if dec.Decode(&m) != nil || m == nil {
		return "", ""
	}
	if dec.Decode(new(any)) != io.EOF {
		return "", ""
	}
	access = firstString(m, "access_token", "accessToken", "token")
	refresh = firstString(m, "refresh_token", "refreshToken")
	if access == "" {
		if d, ok := m["data"].(map[string]any); ok {
			access = firstString(d, "access_token", "accessToken", "token")
			refresh = firstString(d, "refresh_token", "refreshToken")
		}
	}
	return access, refresh
}

func firstString(m map[string]any, keys ...string) string {
	for _, k := range keys {
		if v, ok := m[k]; ok {
			if s := str(v); s != "" {
				return s
			}
		}
	}
	return ""
}

func (c *Client) applyToken(token, refresh string) error {
	c.tokenMu.Lock()
	c.access = token
	if refresh != "" {
		c.refresh = refresh
	}
	c.cookies[accessTokenCookie] = token
	c.tokenMu.Unlock()
	if err := c.saveSession(); err != nil {
		return &APIError{Operation: "session", Kind: "session_persistence_failed", Err: err}
	}
	return nil
}

// --- token helpers -----------------------------------------------------------

// jwtClaims decodes the claims of a JWT without verifying it.
func jwtClaims(token string) map[string]any {
	parts := strings.Split(token, ".")
	if len(parts) < 2 {
		return nil
	}
	b, err := base64.RawURLEncoding.DecodeString(strings.TrimRight(parts[1], "="))
	if err != nil {
		return nil
	}
	var m map[string]any
	if json.Unmarshal(b, &m) != nil {
		return nil
	}
	return m
}

// jwtHash returns the hashed_sub claim used as the PubNub sender id.
func jwtHash(token string) string {
	claims := jwtClaims(token)
	if claims == nil {
		return ""
	}
	return str(claims["hashed_sub"])
}

// tokenExpiry returns the expiry of a JWT token.
func tokenExpiry(token string) (time.Time, bool) {
	if token == "" || !strings.HasPrefix(token, "eyJ") {
		return time.Time{}, false
	}
	claims := jwtClaims(token)
	if claims == nil {
		return time.Time{}, false
	}
	switch v := claims["exp"].(type) {
	case json.Number:
		n, err := v.Int64()
		if err != nil {
			return time.Time{}, false
		}
		return time.Unix(n, 0), true
	case float64:
		return time.Unix(int64(v), 0), true
	}
	return time.Time{}, false
}

// --- shared helpers ----------------------------------------------------------

func object(v any) map[string]any {
	if m, ok := v.(map[string]any); ok {
		return m
	}
	return map[string]any{}
}

func arr(v any) []any {
	if a, ok := v.([]any); ok {
		return a
	}
	return nil
}

func str(v any) string {
	switch x := v.(type) {
	case string:
		return x
	case json.Number:
		return x.String()
	case bool:
		if x {
			return "true"
		}
		return "false"
	default:
		return ""
	}
}

func nested(m map[string]any, ks ...string) any {
	var v any = m
	for _, k := range ks {
		v = object(v)[k]
	}
	return v
}

// findKey performs a deterministic deep lookup: map keys are visited in
// sorted order so the result never depends on Go's map iteration order.
func findKey(v any, key string) any {
	switch x := v.(type) {
	case map[string]any:
		if y, ok := x[key]; ok {
			return y
		}
		keys := make([]string, 0, len(x))
		for k := range x {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		for _, k := range keys {
			if z := findKey(x[k], key); z != nil {
				return z
			}
		}
	case []any:
		for _, y := range x {
			if z := findKey(y, key); z != nil {
				return z
			}
		}
	}
	return nil
}

// findList returns the first list found under key (deterministic order).
func findList(v any, key string) []any {
	switch x := v.(type) {
	case map[string]any:
		if y, ok := x[key]; ok {
			if l, ok := y.([]any); ok {
				return l
			}
		}
		keys := make([]string, 0, len(x))
		for k := range x {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		for _, k := range keys {
			if z := findList(x[k], key); z != nil {
				return z
			}
		}
	case []any:
		for _, y := range x {
			if z := findList(y, key); z != nil {
				return z
			}
		}
	}
	return nil
}

func cleanID(id string) (string, error) {
	if id == "" || len(id) > 180 {
		return "", errors.New("invalid identifier")
	}
	for _, r := range id {
		if !(r >= 'a' && r <= 'z' || r >= 'A' && r <= 'Z' || r >= '0' && r <= '9' || r == '-' || r == '_') {
			return "", errors.New("invalid identifier")
		}
	}
	return id, nil
}

func values(kv ...string) url.Values {
	q := url.Values{}
	for i := 0; i+1 < len(kv); i += 2 {
		if kv[i+1] != "" {
			q.Set(kv[i], kv[i+1])
		}
	}
	return q
}

func cloneValues(q url.Values) url.Values {
	v := url.Values{}
	for k, a := range q {
		v[k] = append([]string{}, a...)
	}
	return v
}
