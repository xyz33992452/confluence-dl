//! Structured progress event emitter for programmatic consumers.
//!
//! When the user passes `--json-events`, the CLI prints one JSON Lines
//! record to **stderr** for each significant milestone (run started, tree
//! discovered, page started, page finished, asset downloaded, errors,
//! completion). Human-readable output continues to go to **stdout** so
//! the two streams stay decoupled and downstream tools can read events
//! cleanly.
//!
//! Events are intentionally small and additive — new fields can be added
//! without breaking existing consumers, but renaming or repurposing an
//! existing field requires a major version bump.

use std::io::Write;
use std::sync::Mutex;

use serde::Serialize;

/// Wire format for a single progress event.
///
/// Only fields that are meaningful for the given `kind` are populated;
/// consumers should treat unknown combinations as best-effort rather than
/// an error.
#[derive(Debug, Serialize)]
pub struct Event<'a> {
  /// Event type, e.g. `"started"`, `"tree"`, `"page_start"`, `"page_done"`,
  /// `"asset"`, `"log"`, `"error"`, `"done"`.
  pub kind: &'a str,
  /// Unix epoch seconds when the event was generated.
  pub ts: u64,
  /// Optional numeric counter (e.g. "this is page 3 of 12").
  #[serde(skip_serializing_if = "Option::is_none")]
  pub index: Option<usize>,
  /// Optional total associated with `index`.
  #[serde(skip_serializing_if = "Option::is_none")]
  pub total: Option<usize>,
  /// Optional human-readable label.
  #[serde(skip_serializing_if = "Option::is_none")]
  pub title: Option<&'a str>,
  /// Optional secondary label (asset name, log level, error origin, ...).
  #[serde(skip_serializing_if = "Option::is_none")]
  pub name: Option<&'a str>,
  /// Optional path (asset output path, page file path).
  #[serde(skip_serializing_if = "Option::is_none")]
  pub path: Option<&'a str>,
  /// Optional detail message (log text, error message).
  #[serde(skip_serializing_if = "Option::is_none")]
  pub message: Option<&'a str>,
  /// Optional kind discriminator for asset events (`"image"` / `"attachment"`).
  #[serde(skip_serializing_if = "Option::is_none")]
  pub asset_kind: Option<&'a str>,
}

/// Sink that serializes events as JSON Lines to a shared writer (stderr).
///
/// A `Mutex` makes the emitter safe to share across the concurrent page
/// download tasks spawned by `download_page_tree`.
pub struct EventSink {
  enabled: bool,
  writer: Mutex<Box<dyn Write + Send + Sync>>,
}

impl EventSink {
  /// Build a sink that writes to the given writer when `enabled` is true.
  ///
  /// When disabled, [`emit`] is a no-op so call sites can stay simple.
  pub fn new(enabled: bool, writer: Box<dyn Write + Send + Sync>) -> Self {
    Self {
      enabled,
      writer: Mutex::new(writer),
    }
  }

  /// Construct a sink wired to stderr.
  pub fn to_stderr(enabled: bool) -> Self {
    Self::new(enabled, Box::new(std::io::stderr()))
  }

  /// True when the sink will actually emit anything.
  pub fn is_enabled(&self) -> bool {
    self.enabled
  }

  /// Serialize and flush a single event.
  ///
  /// Errors are intentionally swallowed — emitting a progress event must
  /// never abort the actual download.
  pub fn emit(&self, event: Event<'_>) {
    if !self.enabled {
      return;
    }
    let Ok(payload) = serde_json::to_string(&event) else {
      return;
    };
    let Ok(mut guard) = self.writer.lock() else {
      return;
    };
    let _ = writeln!(&mut *guard, "{payload}");
    let _ = (&mut *guard).flush();
  }
}

pub fn now_secs() -> u64 {
  std::time::SystemTime::now()
    .duration_since(std::time::UNIX_EPOCH)
    .map(|d| d.as_secs())
    .unwrap_or(0)
}
