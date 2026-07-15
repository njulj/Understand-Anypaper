package api

import (
	"context"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestUploadPaperParsesNDJSONProgress(t *testing.T) {
	tmpDir := t.TempDir()
	filePath := filepath.Join(tmpDir, "paper.pdf")
	if err := os.WriteFile(filePath, []byte("pdf"), 0o644); err != nil {
		t.Fatalf("write temp upload file: %v", err)
	}

	httpClient := &http.Client{
		Transport: roundTripFunc(func(r *http.Request) (*http.Response, error) {
			body := strings.Join([]string{
				`{"event":"upload_received","progress":60,"message":"Upload received."}`,
				`{"event":"complete","progress":100,"message":"Done","graph":{"paper_id":"paper-1","nodes":[],"edges":[]}}`,
				"",
			}, "\n")
			return &http.Response{
				StatusCode: http.StatusOK,
				Header:     http.Header{"Content-Type": []string{"application/x-ndjson"}},
				Body:       io.NopCloser(strings.NewReader(body)),
				Request:    r,
			}, nil
		}),
	}

	client := NewClient("http://example.test", httpClient)
	graph, err := client.UploadPaper(context.Background(), filePath, nil)
	if err != nil {
		t.Fatalf("UploadPaper returned error: %v", err)
	}
	if graph.PaperID != "paper-1" {
		t.Fatalf("unexpected paper id: %s", graph.PaperID)
	}
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (fn roundTripFunc) RoundTrip(r *http.Request) (*http.Response, error) {
	return fn(r)
}
