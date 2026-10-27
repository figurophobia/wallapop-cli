package wallapop

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func TestLate401UsesOutgoingTokenAndSingleRefresh(t *testing.T) {
	var refresh atomic.Int32
	firstOld := make(chan struct{})
	releaseOld := make(chan struct{})
	var oldCount atomic.Int32
	c := fixtureClient(t, func(w http.ResponseWriter, r *http.Request) {
		switch {
		case strings.HasPrefix(r.URL.Path, "/bff/messaging/conversation/"):
			if r.Header.Get("Authorization") == "Bearer new" {
				fmt.Fprint(w, `{"hash":"abc123def456","messages":{"messages":[]}}`)
				return
			}
			if oldCount.Add(1) == 1 {
				close(firstOld)
				<-releaseOld
			}
			w.WriteHeader(401)
		case r.URL.Path == "/api/v3/access/refresh":
			refresh.Add(1)
			fmt.Fprint(w, `{"access_token":"new"}`)
		default:
			http.NotFound(w, r)
		}
	}, false, "old")
	c.refresh = "r1"
	var wg sync.WaitGroup
	errs := make(chan error, 2)
	wg.Add(1)
	go func() { defer wg.Done(); _, err := c.Conversation(context.Background(), "def456abc123"); errs <- err }()
	select {
	case <-firstOld:
	case <-time.After(2 * time.Second):
		t.Fatal("first request did not arrive")
	}
	wg.Add(1)
	go func() { defer wg.Done(); _, err := c.Conversation(context.Background(), "abc123def456"); errs <- err }()
	deadline := time.After(2 * time.Second)
	for refresh.Load() == 0 {
		select {
		case <-deadline:
			t.Fatal("refresh did not start")
		default:
			time.Sleep(time.Millisecond)
		}
	}
	close(releaseOld)
	wg.Wait()
	close(errs)
	for err := range errs {
		if err != nil {
			t.Fatal(err)
		}
	}
	if refresh.Load() != 1 {
		t.Fatalf("refresh calls = %d", refresh.Load())
	}
}

func TestRefreshWaiterCancellationAndRetryAfterTransientFailure(t *testing.T) {
	entered := make(chan struct{})
	release := make(chan struct{})
	var refresh atomic.Int32
	c := fixtureClient(t, func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/bff/messaging/conversation/abc123def456":
			if r.Header.Get("Authorization") == "Bearer new" {
				fmt.Fprint(w, `{"hash":"abc123def456","messages":{"messages":[]}}`)
				return
			}
			w.WriteHeader(401)
		case "/api/v3/access/refresh":
			if refresh.Add(1) == 1 {
				close(entered)
				<-release
				w.WriteHeader(503)
				return
			}
			fmt.Fprint(w, `{"access_token":"new"}`)
		default:
			http.NotFound(w, r)
		}
	}, false, "old")
	c.refresh = "r1"
	ownerDone := make(chan error, 1)
	go func() { _, err := c.Conversation(context.Background(), "abc123def456"); ownerDone <- err }()
	select {
	case <-entered:
	case <-time.After(2 * time.Second):
		t.Fatal("refresh not entered")
	}
	ctx, cancel := context.WithCancel(context.Background())
	waiterDone := make(chan error, 1)
	go func() { _, err := c.Conversation(ctx, "abc123def456"); waiterDone <- err }()
	cancel()
	select {
	case err := <-waiterDone:
		if !errors.Is(err, context.Canceled) {
			t.Fatalf("waiter: %v", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("waiter did not cancel")
	}
	close(release)
	if err := <-ownerDone; err == nil {
		t.Fatal("failed refresh unexpectedly succeeded")
	}
	if _, err := c.Conversation(context.Background(), "abc123def456"); err != nil {
		t.Fatalf("transient refresh permanently cached: %v", err)
	}
	if refresh.Load() != 2 {
		t.Fatalf("refresh calls = %d", refresh.Load())
	}
}

func TestFreshBypassesConversationCacheAndPreservesIdentity(t *testing.T) {
	var calls atomic.Int32
	c := fixtureClient(t, func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		fmt.Fprint(w, `{"hash":"rawhash","with_user":{"hash":"personhash","name":"Ada"},"messages":{"messages":[{"timestamp":1,"text":"hello"}]}}`)
	}, false, "token")
	for _, fresh := range []bool{false, false, true} {
		args, _ := json.Marshal(map[string]any{"conversation_id": "abc123def456", "fresh": fresh})
		v, err := c.Invoke(context.Background(), "get_conversation", args)
		if err != nil {
			t.Fatal(err)
		}
		m := object(v)
		if m["conversation_id"] != "rawhash" || object(m["with_user"])["hash"] != "personhash" || m["history_complete"] != false {
			t.Fatalf("bad conversation: %+v", m)
		}
	}
	if calls.Load() != 2 {
		t.Fatalf("fresh/cache calls = %d", calls.Load())
	}
}

func TestMalformedSellerAndUnreadAreErrors(t *testing.T) {
	c := fixtureClient(t, func(w http.ResponseWriter, r *http.Request) { fmt.Fprint(w, `{}`) }, false, "token")
	if _, err := c.Seller(context.Background(), "abc123def456", false); err == nil {
		t.Fatal("empty seller accepted")
	}
	if _, err := c.Unread(context.Background()); err == nil {
		t.Fatal("empty unread accepted")
	}
}

func TestSameTokenRefreshFallsBackToWeb(t *testing.T) {
	var apiCalls, webCalls atomic.Int32
	c := fixtureClient(t, func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/bff/messaging/conversation/abc123def456":
			if r.Header.Get("Authorization") != "Bearer new" {
				w.WriteHeader(401)
				return
			}
			fmt.Fprint(w, `{"hash":"abc123def456","messages":{"messages":[]}}`)
		case "/api/v3/access/refresh":
			apiCalls.Add(1)
			fmt.Fprint(w, `{"access_token":"old"}`)
		case "/api/auth/session":
			webCalls.Add(1)
			fmt.Fprint(w, `{"accessToken":"new"}`)
		default:
			http.NotFound(w, r)
		}
	}, false, "old")
	c.refresh = "refresh"
	c.cookies[sessionTokenCookie] = "fake-cookie"
	if _, err := c.Conversation(context.Background(), "abc123def456"); err != nil {
		t.Fatal(err)
	}
	if apiCalls.Load() != 1 || webCalls.Load() != 1 {
		t.Fatalf("api=%d web=%d", apiCalls.Load(), webCalls.Load())
	}
}
