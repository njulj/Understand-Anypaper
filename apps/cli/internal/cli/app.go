package cli

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"text/tabwriter"
	"time"

	"github.com/njulj/Understand-Anypaper/internal/api"
	"github.com/njulj/Understand-Anypaper/internal/desktop"
	"github.com/njulj/Understand-Anypaper/internal/render"
	"github.com/njulj/Understand-Anypaper/internal/service"
)

const defaultAPIBaseURL = "http://127.0.0.1:8765"

type App struct {
	stdout io.Writer
	stderr io.Writer
}

type Config struct {
	JSON       bool
	APIBaseURL string
	Timeout    time.Duration
	Workdir    string
	Workspace  string
}

func Run(ctx context.Context, args []string, stdout io.Writer, stderr io.Writer) int {
	app := App{stdout: stdout, stderr: stderr}
	return app.run(ctx, args)
}

func (a App) run(ctx context.Context, args []string) int {
	cfg, rest, err := parseRootFlags(args, a.stderr)
	if err != nil {
		if errors.Is(err, flag.ErrHelp) {
			return 0
		}
		fmt.Fprintln(a.stderr, "Error:", err)
		return 2
	}

	if len(rest) == 0 {
		a.printRootUsage()
		return 0
	}

	ctx, cancel := context.WithTimeout(ctx, cfg.Timeout)
	defer cancel()

	client := api.NewClient(cfg.APIBaseURL, &http.Client{Timeout: cfg.Timeout})

	var commandErr error
	switch rest[0] {
	case "help":
		a.printRootUsage()
		return 0
	case "init":
		commandErr = a.runInit(ctx, cfg, rest[1:])
	case "service":
		commandErr = a.runService(ctx, cfg, rest[1:])
	case "paper":
		commandErr = a.runPaper(ctx, cfg, client, rest[1:])
	case "graph":
		commandErr = a.runGraph(ctx, cfg, client, rest[1:])
	case "node":
		commandErr = a.runNode(ctx, cfg, client, rest[1:])
	case "desktop":
		commandErr = a.runDesktop(ctx, cfg, rest[1:])
	default:
		commandErr = fmt.Errorf("unknown command %q", rest[0])
	}

	if commandErr != nil {
		fmt.Fprintln(a.stderr, "Error:", commandErr)
		return 1
	}
	return 0
}

func parseRootFlags(args []string, stderr io.Writer) (Config, []string, error) {
	flags := flag.NewFlagSet("uap", flag.ContinueOnError)
	flags.SetOutput(stderr)

	var cfg Config
	flags.BoolVar(&cfg.JSON, "json", false, "print machine-readable JSON output")
	flags.StringVar(&cfg.APIBaseURL, "api-base-url", envOrDefault("PAG_API_BASE_URL", defaultAPIBaseURL), "API base URL")
	flags.DurationVar(&cfg.Timeout, "timeout", 30*time.Second, "command timeout")
	flags.StringVar(&cfg.Workspace, "workspace", envOrDefault("PAG_WORKSPACE_DIR", ""), "workspace directory")
	flags.Usage = func() {
		printRootUsage(stderr)
	}
	if err := flags.Parse(args); err != nil {
		return Config{}, nil, err
	}
	workdir, err := os.Getwd()
	if err != nil {
		return Config{}, nil, fmt.Errorf("resolve working directory: %w", err)
	}
	cfg.Workdir = workdir
	return cfg, flags.Args(), nil
}

func (a App) runInit(ctx context.Context, cfg Config, args []string) error {
	flags := flag.NewFlagSet("uap init", flag.ContinueOnError)
	flags.SetOutput(a.stderr)
	path := flags.String("path", cfg.Workspace, "workspace directory")
	if err := flags.Parse(args); err != nil {
		return err
	}
	manager, err := service.NewManager(cfg.Workdir, "")
	if err != nil {
		return err
	}
	result, err := manager.Init(ctx, *path)
	if err != nil {
		return err
	}
	return a.printResult(cfg.JSON, result, func() {
		action := "Initialized"
		if !result.Created {
			action = "Using"
		}
		fmt.Fprintf(a.stdout, "%s workspace at %s\n", action, result.Workspace.Root)
		fmt.Fprintf(a.stdout, "Database: %s\n", result.Workspace.DatabasePath)
	})
}

func (a App) runService(ctx context.Context, cfg Config, args []string) error {
	if len(args) == 0 {
		return errors.New("missing service subcommand")
	}
	manager, err := service.NewManager(cfg.Workdir, "")
	if err != nil {
		return err
	}

	switch args[0] {
	case "start":
		flags := flag.NewFlagSet("uap service start", flag.ContinueOnError)
		flags.SetOutput(a.stderr)
		wait := flags.Bool("wait", true, "wait for service health before returning")
		workspaceDir := flags.String("workspace", cfg.Workspace, "workspace directory")
		settingsPath := flags.String("settings-path", envOrDefault("PAG_DESKTOP_SETTINGS_PATH", ""), "desktop API settings JSON path")
		if err := flags.Parse(args[1:]); err != nil {
			return err
		}
		result, err := manager.Start(ctx, service.Options{
			Wait:         *wait,
			Timeout:      cfg.Timeout,
			WorkspaceDir: *workspaceDir,
			SettingsPath: *settingsPath,
		})
		if err != nil {
			_ = a.printResult(cfg.JSON, result, func() { printServiceCommandResult(a.stdout, result) })
			return err
		}
		return a.printResult(cfg.JSON, result, func() { printServiceCommandResult(a.stdout, result) })
	case "stop":
		flags := flag.NewFlagSet("uap service stop", flag.ContinueOnError)
		flags.SetOutput(a.stderr)
		if err := flags.Parse(args[1:]); err != nil {
			return err
		}
		result, err := manager.Stop(ctx)
		if err != nil {
			return err
		}
		return a.printResult(cfg.JSON, result, func() { printServiceCommandResult(a.stdout, result) })
	case "status":
		status, err := manager.Status(ctx)
		if err != nil {
			return err
		}
		return a.printResult(cfg.JSON, status, func() { printServiceStatus(a.stdout, status) })
	default:
		return fmt.Errorf("unknown service subcommand %q", args[0])
	}
}

func (a App) runPaper(ctx context.Context, cfg Config, client *api.Client, args []string) error {
	if len(args) == 0 {
		return errors.New("missing paper subcommand")
	}
	if err := a.ensureLocalService(ctx, cfg); err != nil {
		return err
	}
	switch args[0] {
	case "upload":
		if len(args) < 2 {
			return errors.New("usage: uap paper upload <file>")
		}
		path, err := filepath.Abs(args[1])
		if err != nil {
			return fmt.Errorf("resolve upload path: %w", err)
		}
		graph, err := client.UploadPaper(ctx, path, func(progress api.UploadStageProgress) {
			if cfg.JSON {
				return
			}
			fmt.Fprintf(a.stdout, "[%3d%%] %s\n", progress.Progress, progress.Message)
		})
		if err != nil {
			return err
		}
		return a.printResult(cfg.JSON, graph, func() { printUploadSummary(a.stdout, path, graph) })
	case "list":
		papers, err := client.ListPapers(ctx)
		if err != nil {
			return err
		}
		return a.printResult(cfg.JSON, papers, func() { printPaperList(a.stdout, papers) })
	case "show":
		if len(args) < 2 {
			return errors.New("usage: uap paper show <paper-id>")
		}
		summary, err := buildPaperSummary(ctx, client, args[1])
		if err != nil {
			return err
		}
		return a.printResult(cfg.JSON, summary, func() { printPaperSummary(a.stdout, summary) })
	case "delete":
		if len(args) < 2 {
			return errors.New("usage: uap paper delete <paper-id>")
		}
		result, err := client.DeletePaper(ctx, args[1])
		if err != nil {
			return err
		}
		return a.printResult(cfg.JSON, result, func() {
			fmt.Fprintf(a.stdout, "Deleted paper %s.\n", args[1])
		})
	default:
		return fmt.Errorf("unknown paper subcommand %q", args[0])
	}
}

func (a App) runGraph(ctx context.Context, cfg Config, client *api.Client, args []string) error {
	if len(args) == 0 {
		return errors.New("missing graph subcommand")
	}
	if err := a.ensureLocalService(ctx, cfg); err != nil {
		return err
	}
	switch args[0] {
	case "show":
		flags := flag.NewFlagSet("uap graph show", flag.ContinueOnError)
		flags.SetOutput(a.stderr)
		rootID := flags.String("root", "", "root node ID")
		depth := flags.Int("depth", 3, "tree depth")
		if err := flags.Parse(args[1:]); err != nil {
			return err
		}
		if len(flags.Args()) < 1 {
			return errors.New("usage: uap graph show <paper-id> [--root <node-id>] [--depth <n>]")
		}
		graph, err := client.GetGraph(ctx, flags.Args()[0])
		if err != nil {
			return err
		}
		if cfg.JSON {
			return a.printJSON(graph)
		}
		fmt.Fprintln(a.stdout, render.GraphTree(graph, *rootID, *depth))
		return nil
	case "search":
		flags := flag.NewFlagSet("uap graph search", flag.ContinueOnError)
		flags.SetOutput(a.stderr)
		var nodeTypes stringSliceFlag
		flags.Var(&nodeTypes, "type", "node type filter; repeatable")
		expandDepth := flags.Int("expand-depth", 1, "expanded subgraph depth")
		if err := flags.Parse(args[1:]); err != nil {
			return err
		}
		if len(flags.Args()) < 2 {
			return errors.New("usage: uap graph search <paper-id> <query> [--type <node-type>]...")
		}
		result, err := client.SearchGraph(ctx, flags.Args()[0], strings.Join(flags.Args()[1:], " "), nodeTypes, *expandDepth)
		if err != nil {
			return err
		}
		return a.printResult(cfg.JSON, result, func() { printSearchResults(a.stdout, result) })
	default:
		return fmt.Errorf("unknown graph subcommand %q", args[0])
	}
}

func (a App) runNode(ctx context.Context, cfg Config, client *api.Client, args []string) error {
	if len(args) == 0 {
		return errors.New("missing node subcommand")
	}
	if err := a.ensureLocalService(ctx, cfg); err != nil {
		return err
	}
	switch args[0] {
	case "evidence":
		if len(args) < 3 {
			return errors.New("usage: uap node evidence <paper-id> <node-id>")
		}
		result, err := client.GetNodeEvidence(ctx, args[1], args[2])
		if err != nil {
			return err
		}
		return a.printResult(cfg.JSON, result, func() { printNodeEvidence(a.stdout, result) })
	default:
		return fmt.Errorf("unknown node subcommand %q", args[0])
	}
}

func (a App) runDesktop(ctx context.Context, cfg Config, args []string) error {
	if len(args) == 0 {
		return errors.New("missing desktop subcommand")
	}
	switch args[0] {
	case "run-backend":
		flags := flag.NewFlagSet("uap desktop run-backend", flag.ContinueOnError)
		flags.SetOutput(a.stderr)
		host := flags.String("host", envOrDefault("PAG_DESKTOP_HOST", "127.0.0.1"), "backend host")
		port := flags.Int("port", envOrDefaultInt("PAG_DESKTOP_PORT", 8765), "backend port")
		documentStoreDir := flags.String("document-store-dir", envOrDefault("PAG_DOCUMENT_STORE_DIR", ""), "document storage directory")
		settingsPath := flags.String("settings-path", envOrDefault("PAG_DESKTOP_SETTINGS_PATH", ""), "desktop API settings JSON path")
		databaseURL := flags.String("database-url", envOrDefault("DATABASE_URL", ""), "database URL for the desktop backend")
		backendMode := flags.String("backend-mode", string(desktop.BackendModeAuto), "backend mode: auto, packaged, or uv")
		backendExecutable := flags.String("backend-executable", envOrDefault("PAG_DESKTOP_BACKEND_EXECUTABLE", ""), "packaged backend executable path")
		serverProjectDir := flags.String("server-project-dir", envOrDefault("PAG_SERVER_PROJECT_DIR", ""), "apps/server project directory for uv fallback")
		dryRun := flags.Bool("dry-run", false, "print the resolved backend command without starting it")
		if err := flags.Parse(args[1:]); err != nil {
			return err
		}

		launchConfig := desktop.LaunchConfig{
			Host:              *host,
			Port:              *port,
			DocumentStoreDir:  *documentStoreDir,
			SettingsPath:      *settingsPath,
			DatabaseURL:       *databaseURL,
			BackendMode:       desktop.BackendMode(*backendMode),
			BackendExecutable: *backendExecutable,
			ServerProjectDir:  *serverProjectDir,
			WorkingDir:        cfg.Workdir,
			Env:               os.Environ(),
			Stdout:            a.stdout,
			Stderr:            a.stderr,
		}

		if *dryRun || cfg.JSON {
			command, _, err := desktop.ResolveCommand(launchConfig)
			if err != nil {
				return err
			}
			return a.printResult(true, command, func() {})
		}
		return desktop.Launch(ctx, launchConfig)
	default:
		return fmt.Errorf("unknown desktop subcommand %q", args[0])
	}
}

func (a App) printResult(asJSON bool, payload any, human func()) error {
	if asJSON {
		return a.printJSON(payload)
	}
	human()
	return nil
}

func (a App) printJSON(payload any) error {
	encoder := json.NewEncoder(a.stdout)
	encoder.SetIndent("", "  ")
	return encoder.Encode(payload)
}

type paperSummaryView struct {
	PaperID         string   `json:"paper_id"`
	Title           string   `json:"title"`
	Abstract        string   `json:"abstract,omitempty"`
	NodeCount       int      `json:"node_count"`
	EdgeCount       int      `json:"edge_count"`
	ContributionIDs []string `json:"contribution_ids"`
	Contributions   []string `json:"contributions"`
}

func buildPaperSummary(ctx context.Context, client *api.Client, paperID string) (paperSummaryView, error) {
	graph, err := client.GetGraph(ctx, paperID)
	if err != nil {
		return paperSummaryView{}, err
	}

	papers, err := client.ListPapers(ctx)
	if err != nil {
		return paperSummaryView{}, err
	}

	summary := paperSummaryView{
		PaperID:   paperID,
		NodeCount: len(graph.Nodes),
		EdgeCount: len(graph.Edges),
	}
	for _, paper := range papers {
		if paper.PaperID == paperID {
			summary.Title = paper.Title
			summary.Abstract = paper.Abstract
			break
		}
	}
	for _, node := range graph.Nodes {
		if node.NodeType == "Contribution" {
			summary.ContributionIDs = append(summary.ContributionIDs, node.ID)
			summary.Contributions = append(summary.Contributions, node.Title)
		}
	}
	sort.Strings(summary.ContributionIDs)
	sort.Strings(summary.Contributions)
	return summary, nil
}

func printPaperList(w io.Writer, papers []api.PaperSummary) {
	if len(papers) == 0 {
		fmt.Fprintln(w, "No papers found.")
		return
	}
	tw := tabwriter.NewWriter(w, 0, 0, 2, ' ', 0)
	fmt.Fprintln(tw, "PAPER ID\tTITLE")
	for _, paper := range papers {
		fmt.Fprintf(tw, "%s\t%s\n", paper.PaperID, paper.Title)
	}
	_ = tw.Flush()
}

func printPaperSummary(w io.Writer, summary paperSummaryView) {
	title := summary.Title
	if title == "" {
		title = "(title unavailable)"
	}
	fmt.Fprintf(w, "Paper: %s\n", title)
	fmt.Fprintf(w, "ID: %s\n", summary.PaperID)
	fmt.Fprintf(w, "Nodes: %d  Edges: %d  Contributions: %d\n", summary.NodeCount, summary.EdgeCount, len(summary.Contributions))
	if summary.Abstract != "" {
		fmt.Fprintf(w, "Abstract: %s\n", compact(summary.Abstract, 220))
	}
	if len(summary.Contributions) > 0 {
		fmt.Fprintln(w, "Contributions:")
		for _, title := range summary.Contributions {
			fmt.Fprintf(w, "  - %s\n", title)
		}
	}
}

func printUploadSummary(w io.Writer, path string, graph api.PaperArgumentGraph) {
	contributionCount := 0
	for _, node := range graph.Nodes {
		if node.NodeType == "Contribution" {
			contributionCount++
		}
	}
	fmt.Fprintf(w, "Uploaded %s\n", path)
	fmt.Fprintf(w, "Paper ID: %s\n", graph.PaperID)
	fmt.Fprintf(w, "Graph: %d nodes, %d edges, %d contributions\n", len(graph.Nodes), len(graph.Edges), contributionCount)
	fmt.Fprintf(w, "Next: uap graph show %s\n", graph.PaperID)
}

func printServiceStatus(w io.Writer, status service.Status) {
	stateText := "stopped"
	if status.Healthy {
		stateText = "running"
	} else if status.Running {
		stateText = "starting"
	}
	fmt.Fprintf(w, "Service: %s\n", stateText)
	tw := tabwriter.NewWriter(w, 0, 0, 2, ' ', 0)
	fmt.Fprintln(tw, "HEALTHY\tAPI\tWORKSPACE\tPID\tDETAIL")
	fmt.Fprintf(tw, "%t\t%s\t%s\t%d\t%s\n", status.Healthy, status.APIBaseURL, emptyFallback(status.WorkspaceDir, "-"), status.PID, status.Detail)
	_ = tw.Flush()
}

func printServiceCommandResult(w io.Writer, result service.CommandResult) {
	fmt.Fprintf(w, "%s\n", result.Message)
	printServiceStatus(w, result.Status)
}

func printSearchResults(w io.Writer, result api.GraphSearchResponse) {
	if len(result.Matches) == 0 {
		fmt.Fprintf(w, "No matches for %q.\n", result.Query)
		return
	}
	for index, match := range result.Matches {
		fmt.Fprintf(w, "%d. [%s] %s (score %.3f, %s)\n", index+1, match.Node.NodeType, match.Node.Title, match.Score, match.Source)
		if match.Node.Summary != "" {
			fmt.Fprintf(w, "   %s\n", compact(match.Node.Summary, 180))
		}
		fmt.Fprintf(w, "   id=%s\n", match.Node.ID)
	}
}

func printNodeEvidence(w io.Writer, result api.NodeEvidenceResponse) {
	fmt.Fprintf(w, "Node ID: %s\n", result.NodeID)
	fmt.Fprintf(w, "Paper ID: %s\n", result.PaperID)
	fmt.Fprintf(w, "Evidence items: %d\n", len(result.Evidence))
	for index, item := range result.Evidence {
		pageText := "?"
		if item.SourceLocation != nil && item.SourceLocation.Page > 0 {
			pageText = fmt.Sprintf("%d", item.SourceLocation.Page)
		}
		fmt.Fprintf(w, "\n%d. [%s] %s (page %s)\n", index+1, item.Role, item.Title, pageText)
		fmt.Fprintf(w, "   %s\n", compact(item.Text, 260))
	}
}

func printRootUsage(w io.Writer) {
	fmt.Fprintln(w, "Understand Anypaper CLI")
	fmt.Fprintln(w)
	fmt.Fprintln(w, "Usage:")
	fmt.Fprintln(w, "  uap [--json] [--api-base-url URL] [--workspace DIR] [--timeout 30s] <command>")
	fmt.Fprintln(w)
	fmt.Fprintln(w, "Commands:")
	fmt.Fprintln(w, "  init")
	fmt.Fprintln(w, "  service start|stop|status")
	fmt.Fprintln(w, "  paper upload|list|show|delete")
	fmt.Fprintln(w, "  graph show|search")
	fmt.Fprintln(w, "  node evidence")
	fmt.Fprintln(w, "  desktop run-backend")
}

func (a App) printRootUsage() {
	printRootUsage(a.stdout)
}

type stringSliceFlag []string

func (s *stringSliceFlag) String() string {
	return strings.Join(*s, ",")
}

func (s *stringSliceFlag) Set(value string) error {
	value = strings.TrimSpace(value)
	if value == "" {
		return errors.New("node type cannot be empty")
	}
	*s = append(*s, value)
	return nil
}

func envOrDefault(key string, fallback string) string {
	if value := strings.TrimSpace(os.Getenv(key)); value != "" {
		return value
	}
	return fallback
}

func compact(text string, limit int) string {
	text = strings.Join(strings.Fields(strings.TrimSpace(text)), " ")
	if limit <= 0 || len(text) <= limit {
		return text
	}
	return text[:limit-1] + "…"
}

func (a App) ensureLocalService(ctx context.Context, cfg Config) error {
	if strings.TrimRight(cfg.APIBaseURL, "/") != defaultAPIBaseURL {
		return nil
	}
	manager, err := service.NewManager(cfg.Workdir, "")
	if err != nil {
		return err
	}
	_, err = manager.EnsureRunning(ctx, service.Options{
		Wait:         true,
		Timeout:      cfg.Timeout,
		WorkspaceDir: cfg.Workspace,
		SettingsPath: envOrDefault("PAG_DESKTOP_SETTINGS_PATH", ""),
	})
	return err
}

func envOrDefaultInt(key string, fallback int) int {
	value := strings.TrimSpace(os.Getenv(key))
	if value == "" {
		return fallback
	}
	parsed, err := strconv.Atoi(value)
	if err != nil {
		return fallback
	}
	return parsed
}

func emptyFallback(value string, fallback string) string {
	if strings.TrimSpace(value) == "" {
		return fallback
	}
	return value
}
