package wallapop

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/jrrome/wallapop-mcp/internal/transport"
)

func fixtureClient(t *testing.T, h http.HandlerFunc, writes bool, token string) *Client {
	t.Helper()
	s := httptest.NewServer(h)
	t.Cleanup(s.Close)
	tr, e := transport.New(transport.Config{HTTPClient: s.Client(), MaxAttempts: 1})
	if e != nil {
		t.Fatal(e)
	}
	t.Cleanup(tr.Close)
	c, e := New(Config{Transport: tr, APIBase: s.URL, WebBase: s.URL, PubNubBase: s.URL, AccessToken: token, EnableWrites: writes})
	if e != nil {
		t.Fatal(e)
	}
	return c
}
func TestSearchEmptyCursorAndLoop(t *testing.T) {
	var n atomic.Int32
	c := fixtureClient(t, func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/v3/search/components":
			w.Write([]byte(`{"search_id":"s"}`))
		case "/api/v3/search/section":
			i := n.Add(1)
			if i == 1 {
				w.Write([]byte(`{"items":[],"next_page":"p2"}`))
			} else {
				w.Write([]byte(`{"items":[{"id":"abc123def456","title":"Camera","price":{"cash":{"amount":12.34,"currency":"EUR"}}}],"next_page":"p2"}`))
			}
		default:
			http.NotFound(w, r)
		}
	}, false, "")
	v, e := c.Search(context.Background(), SearchArgs{Keywords: "camera", Limit: 2, Pages: 2})
	if e == nil || !strings.Contains(e.Error(), "cursor_loop") {
		t.Fatalf("expected cursor loop, got %+v %v", v, e)
	}
	if len(v.Items) != 1 || v.Items[0].Price.String() != "12.34" {
		t.Fatalf("bad items: %+v", v)
	}
}
func TestMutationGateAndNoUnknownArgs(t *testing.T) {
	var calls atomic.Int32
	c := fixtureClient(t, func(w http.ResponseWriter, r *http.Request) { calls.Add(1); w.WriteHeader(204) }, false, "")
	_, e := c.Invoke(context.Background(), "delete_item", json.RawMessage(`{"item_id":"abc123def456"}`))
	if e == nil || calls.Load() != 0 {
		t.Fatalf("write gate failed: %v %d", e, calls.Load())
	}
	_, e = c.Invoke(context.Background(), "set_favorite", json.RawMessage(`{"item_id":"abc123def456","favorite":true}`))
	if e == nil {
		t.Fatal("unknown arg accepted")
	}
}
func TestRefreshOnlyOnSuccess(t *testing.T) {
	var refresh, items atomic.Int32
	c := fixtureClient(t, func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/bff/messaging/conversation/abc123def456":
			items.Add(1)
			if r.Header.Get("Authorization") == "Bearer new" {
				w.Write([]byte(`{"hash":"abc123def456","messages":{"messages":[]}}`))
			} else {
				w.WriteHeader(401)
			}
		case "/api/v3/access/refresh":
			refresh.Add(1)
			w.Write([]byte(`{"data":{"access_token":"new","refresh_token":"r2"}}`))
		default:
			http.NotFound(w, r)
		}
	}, false, "old")
	c.refresh = "r1"
	_, e := c.Conversation(context.Background(), "abc123def456")
	if e != nil || refresh.Load() != 1 || items.Load() != 2 {
		t.Fatalf("refresh failed: %v %d %d", e, refresh.Load(), items.Load())
	}
}
func TestSessionSavePrivatePreservesCookies(t *testing.T) {
	path := filepath.Join(t.TempDir(), "session.json")
	if e := os.WriteFile(path, []byte(`{"accessToken":"old","device_id":"dev","custom":"retain"}`), 0600); e != nil {
		t.Fatal(e)
	}
	tr, e := transport.New(transport.Config{})
	if e != nil {
		t.Fatal(e)
	}
	defer tr.Close()
	c, e := New(Config{Transport: tr, SessionPath: path})
	if e != nil {
		t.Fatal(e)
	}
	c.access = "new"
	c.refresh = "refresh"
	if e = c.saveSession(); e != nil {
		t.Fatal(e)
	}
	st, e := os.Stat(path)
	if e != nil || (runtime.GOOS != "windows" && st.Mode().Perm() != 0600) {
		t.Fatalf("bad perms %v %v", st, e)
	}
	b, _ := os.ReadFile(path)
	if !strings.Contains(string(b), `"custom":"retain"`) || !strings.Contains(string(b), `"device_id":"dev"`) {
		t.Fatalf("lost fields: %s", b)
	}
}
func TestResolveRejectsForeignHost(t *testing.T) {
	c := fixtureClient(t, func(w http.ResponseWriter, r *http.Request) { t.Fatal("network request unexpectedly made") }, false, "")
	_, e := c.ResolveID(context.Background(), "https://evil.example/item/abc123def456")
	if e == nil {
		t.Fatal("foreign URL accepted")
	}
}
