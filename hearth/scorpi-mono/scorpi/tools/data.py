"""Dataset inspection and charts."""

import os
import time

from . import Result, ToolError, resolve_path, tool

PALETTE = ["#3E6B5A", "#C07A55", "#5B7FA3", "#A98B3C", "#8A6A8E", "#6E8F7E", "#B4645F", "#7C8A96"]


def load_frame(path, sheet=None):
    import pandas as pd
    ext = os.path.splitext(path)[1].lower()
    if ext in (".csv", ".txt"):
        return pd.read_csv(path, sep=None, engine="python")
    if ext == ".tsv":
        return pd.read_csv(path, sep="\t")
    if ext in (".xlsx", ".xlsm", ".xls"):
        return pd.read_excel(path, sheet_name=sheet or 0)
    if ext == ".json":
        try:
            return pd.read_json(path)
        except ValueError:
            return pd.read_json(path, lines=True)
    if ext == ".parquet":
        return pd.read_parquet(path)
    raise ToolError("Unsupported data format %s (use csv, tsv, xlsx, json, parquet)." % ext)


@tool(
    "analyze_data",
    "Load a dataset (CSV, Excel, JSON) and report its shape, columns, types, missing values, statistics and sample rows. "
    "Optionally run a pandas expression on it (the DataFrame is named df), e.g. \"df.groupby('city')['sales'].sum()\".",
    {"path": "string: Data file", "sheet?": "string: Excel sheet", "expression?": "string: Optional pandas expression using df"},
    group="data",
    requires=("pandas",),
)
def analyze_data(ctx, path, sheet="", expression=""):
    import pandas as pd
    path = resolve_path(path, must_exist=True)
    df = load_frame(path, sheet or None)
    pd.set_option("display.width", 160)
    pd.set_option("display.max_columns", 30)
    parts = [
        "Dataset: %s" % path,
        "Shape: %d rows x %d columns" % df.shape,
        "Columns and types:\n" + "\n".join("  %s: %s (missing %d)" % (c, t, df[c].isna().sum()) for c, t in df.dtypes.items()),
    ]
    numeric = df.select_dtypes("number")
    if not numeric.empty:
        parts.append("Numeric summary:\n" + numeric.describe().T.round(3).to_string())
    text_cols = list(df.select_dtypes(exclude=["number", "datetime"]).columns)[:6]
    for col in text_cols:
        top = df[col].value_counts().head(5)
        parts.append("Top values of %s:\n%s" % (col, top.to_string()))
    parts.append("First rows:\n" + df.head(8).to_string())
    if expression:
        try:
            value = eval(expression, {"__builtins__": {}}, {"df": df, "pd": pd})  # noqa: S307 - local, user-owned data
            parts.append("Result of %s:\n%s" % (expression, value.to_string() if hasattr(value, "to_string") else value))
        except Exception as exc:
            parts.append("Expression failed: %s" % exc)
    table = {"type": "table", "columns": [str(c) for c in df.columns[:12]], "rows": df.head(12).astype(str).values.tolist()[:12], "caption": "%s · %d rows" % (os.path.basename(path), len(df))}
    return Result("\n\n".join(parts)[:30000], display=table)


@tool(
    "make_chart",
    "Draw a clean chart and save it as PNG (shown to the user). Either give a data file plus column names, or inline "
    "labels/series. Kinds: bar, barh, line, area, scatter, pie, hist.",
    {
        "kind": "string[bar,barh,line,area,scatter,pie,hist]: Chart type",
        "title?": "string: Chart title",
        "path?": "string: Data file (csv/xlsx/json)",
        "x?": "string: Column for x / labels",
        "y?": "array<string>: Column(s) for values",
        "labels?": "array<string>: Inline labels (if no file)",
        "series?": {"type": "object", "description": "Inline data: {series name: [numbers]}"},
        "aggregate?": "string[none,sum,mean,count]: Group by x and aggregate",
        "output?": "string: PNG path (default workspace/charts)",
    },
    group="data",
    requires=("matplotlib",),
)
def make_chart(ctx, kind, title="", path="", x="", y=None, labels=None, series=None, aggregate="none", output=""):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if path:
        df = load_frame(resolve_path(path, must_exist=True))
        ys = y or [c for c in df.select_dtypes("number").columns if c != x][:3]
        if x and aggregate and aggregate != "none":
            df = getattr(df.groupby(x)[ys], aggregate)().reset_index()
        labels = df[x].astype(str).tolist() if x else [str(i) for i in range(len(df))]
        series = {col: df[col].tolist() for col in ys}
    if not series:
        raise ToolError("Provide a data file with columns or inline series.")
    labels = labels or [str(i + 1) for i in range(len(next(iter(series.values()))))]

    plt.rcParams.update({
        "font.family": "sans-serif", "font.size": 10, "axes.edgecolor": "#BDB7AB", "axes.labelcolor": "#3A3F3B",
        "xtick.color": "#5E645F", "ytick.color": "#5E645F", "axes.spines.top": False, "axes.spines.right": False,
        "axes.grid": True, "grid.color": "#E6E1D7", "grid.linewidth": 0.8, "axes.axisbelow": True,
    })
    fig, ax = plt.subplots(figsize=(8, 4.6), dpi=150)
    fig.patch.set_facecolor("#FBF9F4")
    ax.set_facecolor("#FBF9F4")
    names = list(series)
    if kind == "pie":
        values = series[names[0]]
        ax.pie(values, labels=labels, colors=PALETTE, autopct="%1.0f%%", startangle=90,
               wedgeprops={"linewidth": 2, "edgecolor": "#FBF9F4"}, textprops={"color": "#2B2F2C"})
        ax.axis("equal")
        ax.grid(False)
    elif kind == "hist":
        for i, name in enumerate(names):
            ax.hist(series[name], bins=20, color=PALETTE[i % len(PALETTE)], alpha=0.75, label=name)
    else:
        count = len(names)
        positions = range(len(labels))
        width = 0.8 / max(count, 1)
        for i, name in enumerate(names):
            values = series[name]
            color = PALETTE[i % len(PALETTE)]
            if kind == "bar":
                ax.bar([p + (i - (count - 1) / 2) * width for p in positions], values, width * 0.92, label=name, color=color)
            elif kind == "barh":
                ax.barh([p + (i - (count - 1) / 2) * width for p in positions], values, width * 0.92, label=name, color=color)
            elif kind == "scatter":
                ax.scatter(range(len(values)), values, label=name, color=color, s=28)
            elif kind == "area":
                ax.fill_between(range(len(values)), values, alpha=0.35, color=color)
                ax.plot(range(len(values)), values, label=name, color=color, linewidth=2)
            else:
                ax.plot(range(len(values)), values, label=name, color=color, linewidth=2.2, marker="o", markersize=3.5)
        ticks = list(positions)
        step = max(1, len(ticks) // 14)
        if kind == "barh":
            ax.set_yticks(ticks[::step])
            ax.set_yticklabels(labels[::step])
        else:
            ax.set_xticks(ticks[::step])
            ax.set_xticklabels(labels[::step], rotation=30 if len(labels) > 6 else 0, ha="right" if len(labels) > 6 else "center")
    if title:
        ax.set_title(title, loc="left", fontsize=13, color="#1F2420", pad=12, fontweight="bold")
    if len(names) > 1 and kind != "pie":
        ax.legend(frameon=False)
    fig.tight_layout()
    out = resolve_path(output) if output else resolve_path(os.path.join("charts", "chart-%s.png" % time.strftime("%Y%m%d-%H%M%S")))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    fig.savefig(out, facecolor=fig.get_facecolor())
    plt.close(fig)
    return Result("Chart saved to %s" % out, display={"type": "image", "path": out, "caption": title}, files=[out])
