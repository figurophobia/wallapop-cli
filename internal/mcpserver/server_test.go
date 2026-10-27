package mcpserver

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

type backendFunc func(context.Context, string, json.RawMessage) (any, error)

func (f backendFunc) Invoke(ctx context.Context, op string, args json.RawMessage) (any, error) {
	return f(ctx, op, args)
}

func connect(t *testing.T, backend Backend, opts Options) *mcp.ClientSession {
	t.Helper()
	s, err := New(backend, opts)
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	t.Cleanup(cancel)
	st, ct := mcp.NewInMemoryTransports()
	ss, err := s.Connect(ctx, st, nil)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { ss.Close() })
	c := mcp.NewClient(&mcp.Implementation{Name: "test", Version: "1"}, nil)
	cs, err := c.Connect(ctx, ct, nil)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { cs.Close() })
	return cs
}

func TestDiscoveryAndWriteGate(t *testing.T) {
	for _, writes := range []bool{false, true} {
		cs := connect(t, backendFunc(func(context.Context, string, json.RawMessage) (any, error) { return map[string]bool{"ok": true}, nil }), Options{EnableWrites: writes})
		result, err := cs.ListTools(context.Background(), nil)
		if err != nil {
			t.Fatal(err)
		}
		found := false
		for _, tool := range result.Tools {
			if tool.Name == "delete_item" {
				found = true
				if tool.Annotations.ReadOnlyHint {
					t.Fatal("write tool marked read-only")
				}
			}
		}
		if found != writes {
			t.Fatalf("writes=%v, delete exposed=%v", writes, found)
		}
		if !writes {
			r, err := cs.CallTool(context.Background(), &mcp.CallToolParams{Name: "delete_item", Arguments: map[string]any{"item_id": "abcdefghijkl"}})
			if err == nil && !r.IsError {
				t.Fatal("hidden write executable")
			}
		}
	}
}

func TestValidationDoesNotInvokeBackend(t *testing.T) {
	var called atomic.Int32
	cs := connect(t, backendFunc(func(context.Context, string, json.RawMessage) (any, error) {
		called.Add(1)
		return map[string]bool{"ok": true}, nil
	}), Options{EnableWrites: true})
	for _, tc := range []struct {
		name string
		args any
	}{
		{"search", map[string]any{"keywords": "bike", "pages": 100}},
		{"search", map[string]any{"keywords": "bike", "unrecognized": "secret"}},
		{"publish_item", map[string]any{"price": 19.95}},
		{"set_favorite", map[string]any{"item_id": "x"}},
	} {
		r, err := cs.CallTool(context.Background(), &mcp.CallToolParams{Name: tc.name, Arguments: tc.args})
		if err == nil && !r.IsError {
			t.Fatalf("accepted invalid args for %s", tc.name)
		}
	}
	if called.Load() != 0 {
		t.Fatal("invalid arguments reached backend")
	}
}

func TestStructuredResultsAndErrors(t *testing.T) {
	cs := connect(t, backendFunc(func(_ context.Context, op string, _ json.RawMessage) (any, error) {
		if op == "get_item" {
			return nil, errors.New("listing unavailable")
		}
		return map[string]any{"items": []any{}, "has_more": false}, nil
	}), Options{})
	r, err := cs.CallTool(context.Background(), &mcp.CallToolParams{Name: "search", Arguments: map[string]any{"keywords": "bike"}})
	if err != nil || r.IsError || r.StructuredContent == nil {
		t.Fatalf("structured success: %v %v", r, err)
	}
	r, err = cs.CallTool(context.Background(), &mcp.CallToolParams{Name: "get_item", Arguments: map[string]any{"item_id": "abc"}})
	if err != nil || !r.IsError || r.StructuredContent == nil {
		t.Fatalf("structured failure: %v %v", r, err)
	}
}

func TestOutputBudget(t *testing.T) {
	cs := connect(t, backendFunc(func(context.Context, string, json.RawMessage) (any, error) {
		return map[string]string{"payload": strings.Repeat("x", 2048)}, nil
	}), Options{MaxOutputBytes: 1024})
	r, err := cs.CallTool(context.Background(), &mcp.CallToolParams{Name: "session_status", Arguments: map[string]any{}})
	if err != nil || !r.IsError {
		t.Fatalf("expected output limit: %v %v", r, err)
	}
}

func TestDeadline(t *testing.T) {
	cs := connect(t, backendFunc(func(ctx context.Context, _ string, _ json.RawMessage) (any, error) {
		<-ctx.Done()
		return nil, ctx.Err()
	}), Options{Timeout: 20 * time.Millisecond})
	r, err := cs.CallTool(context.Background(), &mcp.CallToolParams{Name: "session_status", Arguments: map[string]any{}})
	if err != nil || !r.IsError {
		t.Fatalf("expected deadline error: %v %v", r, err)
	}
}

func BenchmarkServerConstruction(b *testing.B) {
	backend := backendFunc(func(context.Context, string, json.RawMessage) (any, error) {
		return map[string]bool{"logged_in": false}, nil
	})
	for b.Loop() {
		if _, err := New(backend, Options{}); err != nil {
			b.Fatal(err)
		}
	}
}

type unknownWrite struct{}

func (unknownWrite) Error() string        { return "outcome unknown; check state before retrying" }
func (unknownWrite) OutcomeUnknown() bool { return true }

func TestUnknownWriteIsMachineReadable(t *testing.T) {
	cs := connect(t, backendFunc(func(context.Context, string, json.RawMessage) (any, error) { return nil, unknownWrite{} }), Options{EnableWrites: true})
	r, err := cs.CallTool(context.Background(), &mcp.CallToolParams{Name: "send_message", Arguments: map[string]any{"item_id": "abcdefghijkl", "message": "hello"}})
	if err != nil || !r.IsError {
		t.Fatalf("expected error: %v %v", r, err)
	}
	b, _ := json.Marshal(r.StructuredContent)
	if !strings.Contains(string(b), `"outcome_unknown":true`) {
		t.Fatalf("lost ambiguous outcome: %s", b)
	}
}

func TestCategoriesHaveObjectEnvelope(t *testing.T) {
	cs := connect(t, backendFunc(func(context.Context, string, json.RawMessage) (any, error) { return []any{}, nil }), Options{})
	r, err := cs.CallTool(context.Background(), &mcp.CallToolParams{Name: "list_categories", Arguments: map[string]any{}})
	if err != nil || r.IsError {
		t.Fatalf("categories: %v %v", r, err)
	}
	b, _ := json.Marshal(r.StructuredContent)
	if string(b) != `{"categories":[]}` {
		t.Fatalf("envelope=%s", b)
	}
}

func TestErrorPreservesPartialResource(t *testing.T) {
	cs := connect(t, backendFunc(func(context.Context, string, json.RawMessage) (any, error) {
		return map[string]any{"item_id": "created", "completed_photos": 1, "partial": true}, context.DeadlineExceeded
	}), Options{EnableWrites: true})
	r, err := cs.CallTool(context.Background(), &mcp.CallToolParams{Name: "publish_item", Arguments: map[string]any{
		"title": "Camera", "description": "Used camera", "price": "19.95", "category_leaf_id": "10088", "condition": "good", "color": "black", "photo_paths": []string{"photo.jpg"}, "shipping_allowed": false,
	}})
	if err != nil || !r.IsError {
		t.Fatalf("expected partial error: %v %v", r, err)
	}
	b, _ := json.Marshal(r.StructuredContent)
	if !strings.Contains(string(b), `"item_id":"created"`) {
		t.Fatalf("lost created resource: %s", b)
	}
}

func TestNonObjectResultHasEnvelope(t *testing.T) {
	for _, result := range []any{nil, true, []any{"accepted"}} {
		cs := connect(t, backendFunc(func(context.Context, string, json.RawMessage) (any, error) { return result, nil }), Options{EnableWrites: true})
		r, err := cs.CallTool(context.Background(), &mcp.CallToolParams{Name: "set_favorite", Arguments: map[string]any{"item_id": "abc", "favorited": true}})
		if err != nil || r.IsError {
			t.Fatalf("non-object result: %v %v", r, err)
		}
		b, _ := json.Marshal(r.StructuredContent)
		var value map[string]any
		if err := json.Unmarshal(b, &value); err != nil {
			t.Fatal(err)
		}
		if _, ok := value["result"]; !ok {
			t.Fatalf("missing result envelope: %s", b)
		}
	}
}
