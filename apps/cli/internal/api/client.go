package api

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"mime/multipart"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
)

const defaultMaxProgressLineBytes = 1024 * 1024

type Client struct {
	baseURL    string
	httpClient *http.Client
}

func NewClient(baseURL string, httpClient *http.Client) *Client {
	if httpClient == nil {
		httpClient = http.DefaultClient
	}
	return &Client{
		baseURL:    strings.TrimRight(baseURL, "/"),
		httpClient: httpClient,
	}
}

type PaperSummary struct {
	PaperID  string         `json:"paper_id"`
	Title    string         `json:"title"`
	Abstract string         `json:"abstract"`
	Metadata map[string]any `json:"metadata,omitempty"`
}

type GraphNode struct {
	ID              string         `json:"id"`
	PaperID         string         `json:"paper_id"`
	NodeType        string         `json:"node_type"`
	Title           string         `json:"title"`
	Summary         string         `json:"summary"`
	Confidence      float64        `json:"confidence"`
	SourceType      string         `json:"source_type"`
	SemanticUnitIDs []string       `json:"semantic_unit_ids"`
	PageRanges      [][2]int       `json:"page_ranges"`
	Properties      map[string]any `json:"properties"`
	CreatedBy       string         `json:"created_by"`
	Verified        bool           `json:"verified"`
}

type GraphEdge struct {
	ID              string         `json:"id"`
	PaperID         string         `json:"paper_id"`
	SourceNodeID    string         `json:"source_node_id"`
	TargetNodeID    string         `json:"target_node_id"`
	EdgeType        string         `json:"edge_type"`
	Confidence      float64        `json:"confidence"`
	SemanticUnitIDs []string       `json:"semantic_unit_ids"`
	InferenceType   string         `json:"inference_type"`
	Properties      map[string]any `json:"properties"`
}

type PaperArgumentGraph struct {
	PaperID string      `json:"paper_id"`
	Nodes   []GraphNode `json:"nodes"`
	Edges   []GraphEdge `json:"edges"`
}

type UploadStageProgress struct {
	Event             string              `json:"event"`
	Progress          int                 `json:"progress"`
	Message           string              `json:"message"`
	Graph             *PaperArgumentGraph `json:"graph,omitempty"`
	PageCount         int                 `json:"page_count,omitempty"`
	SemanticUnitCount int                 `json:"semantic_unit_count,omitempty"`
	NodeCount         int                 `json:"node_count,omitempty"`
	EdgeCount         int                 `json:"edge_count,omitempty"`
}

type NodeEvidenceResponse struct {
	NodeID          string         `json:"node_id"`
	PaperID         string         `json:"paper_id"`
	SemanticUnitIDs []string       `json:"semantic_unit_ids"`
	PageRanges      [][2]int       `json:"page_ranges"`
	Evidence        []EvidenceItem `json:"evidence"`
}

type EvidenceItem struct {
	SemanticUnitID string          `json:"semantic_unit_id"`
	Role           string          `json:"role"`
	Title          string          `json:"title"`
	Text           string          `json:"text"`
	SourceLocation *SourceLocation `json:"source_location"`
}

type SourceLocation struct {
	Page             int             `json:"page"`
	BBox             []float64       `json:"bbox"`
	ExtractedText    string          `json:"extracted_text"`
	BlockID          string          `json:"block_id"`
	StartOffset      int             `json:"start_offset"`
	EndOffset        int             `json:"end_offset"`
	ExtractionMethod string          `json:"extraction_method"`
	Segments         []SourceSegment `json:"segments"`
}

type SourceSegment struct {
	Page             int       `json:"page"`
	BBox             []float64 `json:"bbox"`
	ExtractedText    string    `json:"extracted_text"`
	BlockID          string    `json:"block_id"`
	StartOffset      int       `json:"start_offset"`
	EndOffset        int       `json:"end_offset"`
	ExtractionMethod string    `json:"extraction_method"`
}

type GraphSearchMatch struct {
	Node   GraphNode `json:"node"`
	Score  float64   `json:"score"`
	Source string    `json:"source"`
}

type ExpandedSubgraph struct {
	Nodes []GraphNode `json:"nodes"`
	Edges []GraphEdge `json:"edges"`
	Depth int         `json:"depth"`
}

type GraphSearchResponse struct {
	Query            string             `json:"query"`
	Matches          []GraphSearchMatch `json:"matches"`
	ExpandedSubgraph ExpandedSubgraph   `json:"expanded_subgraph"`
}

func (c *Client) ListPapers(ctx context.Context) ([]PaperSummary, error) {
	var result []PaperSummary
	if err := c.doJSON(ctx, http.MethodGet, "/api/papers", nil, &result); err != nil {
		return nil, err
	}
	return result, nil
}

func (c *Client) DeletePaper(ctx context.Context, paperID string) (map[string]any, error) {
	var result map[string]any
	if err := c.doJSON(ctx, http.MethodDelete, "/api/papers/"+url.PathEscape(paperID), nil, &result); err != nil {
		return nil, err
	}
	return result, nil
}

func (c *Client) GetGraph(ctx context.Context, paperID string) (PaperArgumentGraph, error) {
	var result PaperArgumentGraph
	err := c.doJSON(ctx, http.MethodGet, "/api/papers/"+url.PathEscape(paperID)+"/graph", nil, &result)
	return result, err
}

func (c *Client) SearchGraph(ctx context.Context, paperID string, query string, nodeTypes []string, expandDepth int) (GraphSearchResponse, error) {
	body := map[string]any{
		"paper_id":     paperID,
		"query":        query,
		"node_types":   nodeTypes,
		"expand_depth": expandDepth,
	}
	var result GraphSearchResponse
	err := c.doJSON(ctx, http.MethodPost, "/api/graph/search", body, &result)
	return result, err
}

func (c *Client) GetNodeEvidence(ctx context.Context, paperID string, nodeID string) (NodeEvidenceResponse, error) {
	var result NodeEvidenceResponse
	path := fmt.Sprintf("/api/nodes/%s/evidence?paper_id=%s", url.PathEscape(nodeID), url.QueryEscape(paperID))
	err := c.doJSON(ctx, http.MethodGet, path, nil, &result)
	return result, err
}

func (c *Client) UploadPaper(ctx context.Context, path string, onProgress func(UploadStageProgress)) (PaperArgumentGraph, error) {
	file, err := os.Open(path)
	if err != nil {
		return PaperArgumentGraph{}, fmt.Errorf("open upload file: %w", err)
	}
	defer file.Close()

	bodyReader, contentType, err := createUploadBody(filepath.Base(path), file)
	if err != nil {
		return PaperArgumentGraph{}, err
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+"/api/papers", bodyReader)
	if err != nil {
		return PaperArgumentGraph{}, fmt.Errorf("create upload request: %w", err)
	}
	req.Header.Set("Content-Type", contentType)

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return PaperArgumentGraph{}, fmt.Errorf("upload paper: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		payload, _ := io.ReadAll(resp.Body)
		return PaperArgumentGraph{}, fmt.Errorf("upload failed with HTTP %d: %s", resp.StatusCode, strings.TrimSpace(string(payload)))
	}

	scanner := bufio.NewScanner(resp.Body)
	scanner.Buffer(make([]byte, 0, 8*1024), defaultMaxProgressLineBytes)

	var finalGraph *PaperArgumentGraph
	for scanner.Scan() {
		line := scanner.Bytes()
		if len(bytes.TrimSpace(line)) == 0 {
			continue
		}
		var progress UploadStageProgress
		if err := json.Unmarshal(line, &progress); err != nil {
			return PaperArgumentGraph{}, fmt.Errorf("parse upload progress: %w", err)
		}
		if onProgress != nil {
			onProgress(progress)
		}
		if progress.Event == "error" {
			return PaperArgumentGraph{}, errors.New(progress.Message)
		}
		if progress.Event == "complete" && progress.Graph != nil {
			finalGraph = progress.Graph
		}
	}
	if err := scanner.Err(); err != nil {
		return PaperArgumentGraph{}, fmt.Errorf("read upload progress: %w", err)
	}
	if finalGraph == nil {
		return PaperArgumentGraph{}, errors.New("upload finished without a graph result")
	}
	return *finalGraph, nil
}

func createUploadBody(filename string, file *os.File) (io.Reader, string, error) {
	pipeReader, pipeWriter := io.Pipe()
	writer := multipart.NewWriter(pipeWriter)

	go func() {
		defer pipeWriter.Close()
		defer writer.Close()

		part, err := writer.CreateFormFile("file", filename)
		if err != nil {
			_ = pipeWriter.CloseWithError(fmt.Errorf("create upload form field: %w", err))
			return
		}
		if _, err := io.Copy(part, file); err != nil {
			_ = pipeWriter.CloseWithError(fmt.Errorf("stream upload file: %w", err))
		}
	}()

	return pipeReader, writer.FormDataContentType(), nil
}

func (c *Client) doJSON(ctx context.Context, method string, path string, body any, out any) error {
	var payload io.Reader
	if body != nil {
		encoded, err := json.Marshal(body)
		if err != nil {
			return fmt.Errorf("encode request body: %w", err)
		}
		payload = bytes.NewReader(encoded)
	}

	req, err := http.NewRequestWithContext(ctx, method, c.baseURL+path, payload)
	if err != nil {
		return fmt.Errorf("create request: %w", err)
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return fmt.Errorf("%s %s: %w", method, path, err)
	}
	defer resp.Body.Close()

	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		payload, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("request failed with HTTP %d: %s", resp.StatusCode, strings.TrimSpace(string(payload)))
	}
	if out == nil {
		return nil
	}
	if err := json.NewDecoder(resp.Body).Decode(out); err != nil {
		return fmt.Errorf("decode response body: %w", err)
	}
	return nil
}
