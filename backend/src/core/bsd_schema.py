"""Canonical schema for .bsd screen specs.

Every screen stored in ``design/screens/<slug>.bsd`` must conform to this
schema. It's a deliberately small, Pencil-Dev-style vocabulary so the
Designer agent, the ScreenRenderer UI, and anyone inspecting the file by
hand see the exact same shape.

Design goals:
  - One canonical tree structure: ``{type, props, children}``
  - A fixed set of component types — no ad-hoc "TodoList" / "Fab" types
  - A fixed per-type list of allowed non-style props
  - A fixed vocabulary for style keys (React-Native-ish, web-renderable)

The validator accepts minor alternatives (``text`` attr at top level, string
children for convenience) but normalises them to the canonical form.

Passing ``strict=True`` rejects any unknown type or prop with a helpful
error message that the LLM can act on.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# ── Component vocabulary ────────────────────────────────────────────

# Layout containers
LAYOUT_TYPES = {"Screen", "View", "Row", "ScrollView"}

# Text
TEXT_TYPES = {"Text", "Heading"}

# Interactive
INPUT_TYPES = {"Button", "TextInput", "Checkbox", "Switch"}

# Display
DISPLAY_TYPES = {"Image", "Icon", "Badge", "Card", "Divider"}

# Lists
LIST_TYPES = {"List", "ListItem"}

# Navigation / chrome
NAV_TYPES = {"Appbar", "Tab", "Fab"}

ALLOWED_TYPES: set[str] = (
    LAYOUT_TYPES | TEXT_TYPES | INPUT_TYPES | DISPLAY_TYPES | LIST_TYPES | NAV_TYPES
)


# Per-type allowed non-style props. `style` and `children` are allowed on
# every type and are not listed here.
ALLOWED_PROPS: dict[str, set[str]] = {
    "Screen":     {"title"},
    "View":       set(),
    "Row":        set(),
    "ScrollView": set(),
    "Text":       {"text"},
    "Heading":    {"text", "level"},  # level: 1..3
    "Button":     {"text", "variant", "icon", "disabled"},  # variant: primary|secondary|danger|ghost
    "TextInput":  {"label", "placeholder", "value", "multiline", "secureTextEntry"},
    "Checkbox":   {"label", "checked"},
    "Switch":     {"label", "value"},
    "Image":      {"src", "alt"},
    "Icon":       {"name"},
    "Badge":      {"text", "variant"},  # variant: neutral|info|success|warning|danger
    "Card":       {"title", "subtitle"},
    "Divider":    set(),
    "List":       set(),
    "ListItem":   {"text", "description", "completed", "icon"},
    "Appbar":     {"title"},
    "Tab":        {"label", "active"},
    "Fab":        {"icon", "label"},
}


# Allowed style keys (RN-style; renderable on web). Unknown style keys are
# rejected to keep designs portable and consistent.
ALLOWED_STYLE_KEYS: set[str] = {
    # Flex
    "flex", "flexDirection", "flexWrap", "flexGrow", "flexShrink", "flexBasis",
    "justifyContent", "alignItems", "alignSelf", "gap", "rowGap", "columnGap",
    # Spacing
    "padding", "paddingTop", "paddingBottom", "paddingLeft", "paddingRight",
    "paddingHorizontal", "paddingVertical",
    "margin", "marginTop", "marginBottom", "marginLeft", "marginRight",
    "marginHorizontal", "marginVertical",
    # Sizing
    "width", "height", "minWidth", "minHeight", "maxWidth", "maxHeight",
    # Colors / borders / background
    "backgroundColor", "color", "borderColor",
    "borderWidth", "borderTopWidth", "borderBottomWidth",
    "borderLeftWidth", "borderRightWidth", "borderRadius",
    # Typography
    "fontSize", "fontWeight", "fontStyle", "fontFamily",
    "lineHeight", "letterSpacing", "textAlign", "textTransform",
    # Misc
    "opacity", "position", "top", "right", "bottom", "left", "overflow",
}


# Shape of spec root — same as any node, but must be a layout type.
ROOT_ALLOWED_TYPES = {"Screen", "View", "ScrollView"}


# ── Validation ──────────────────────────────────────────────────────


@dataclass
class ValidationError:
    path: str
    message: str

    def format(self) -> str:
        return f"{self.path}: {self.message}"


def validate_spec(spec: Any, *, strict: bool = True) -> list[ValidationError]:
    """Validate a .bsd spec tree against the canonical schema.

    Returns a list of :class:`ValidationError`. An empty list means the spec
    is valid. With ``strict=False``, unknown component types and style keys
    are reported as errors but non-fatal ones (content is still renderable).
    """
    errors: list[ValidationError] = []
    if not isinstance(spec, dict):
        errors.append(ValidationError("spec", f"must be an object, got {type(spec).__name__}"))
        return errors

    # Accept either {root: {...}} or a direct node at the top
    root = spec.get("root") if "root" in spec else spec
    _validate_node(root, path="spec", errors=errors, is_root=True, strict=strict)
    return errors


def _validate_node(node: Any, *, path: str, errors: list[ValidationError], is_root: bool, strict: bool) -> None:
    if node is None:
        errors.append(ValidationError(path, "node is null"))
        return
    if isinstance(node, (str, int, float)):
        # Primitives only allowed inside children arrays (handled by caller)
        errors.append(ValidationError(path, f"expected object with `type`, got {type(node).__name__}"))
        return
    if not isinstance(node, dict):
        errors.append(ValidationError(path, f"expected object with `type`, got {type(node).__name__}"))
        return

    node_type = node.get("type")
    if not isinstance(node_type, str):
        errors.append(ValidationError(path, "`type` is required and must be a string"))
        return

    if node_type not in ALLOWED_TYPES:
        suggestion = _suggest_type(node_type)
        msg = (
            f"unknown component type `{node_type}`. Allowed types: "
            f"{', '.join(sorted(ALLOWED_TYPES))}."
        )
        if suggestion:
            msg += f" Did you mean `{suggestion}`?"
        errors.append(ValidationError(path, msg))
        if strict:
            return  # skip further validation on this unknown node

    if is_root and node_type not in ROOT_ALLOWED_TYPES:
        errors.append(ValidationError(
            path,
            f"root type `{node_type}` not allowed; use one of: "
            f"{', '.join(sorted(ROOT_ALLOWED_TYPES))}.",
        ))

    # Validate props
    props = node.get("props")
    if props is not None and not isinstance(props, dict):
        errors.append(ValidationError(f"{path}.props", "must be an object"))
    else:
        allowed = ALLOWED_PROPS.get(node_type, set())
        if isinstance(props, dict):
            for k, v in props.items():
                if k == "style":
                    _validate_style(v, path=f"{path}.props.style", errors=errors)
                elif k not in allowed:
                    errors.append(ValidationError(
                        f"{path}.props.{k}",
                        f"prop `{k}` not allowed on `{node_type}`. "
                        f"Allowed props: {sorted(allowed) or '(only style + children)'}",
                    ))

    # Validate children
    children = node.get("children")
    if children is not None:
        if isinstance(children, list):
            for i, child in enumerate(children):
                if isinstance(child, (str, int, float)):
                    # OK — text leaf under Text/Heading/Button/ListItem
                    continue
                _validate_node(
                    child,
                    path=f"{path}.children[{i}]",
                    errors=errors,
                    is_root=False,
                    strict=strict,
                )
        elif isinstance(children, (str, int, float)):
            # OK — single text child
            pass
        else:
            errors.append(ValidationError(f"{path}.children", "must be a list or a string"))


def _validate_style(style: Any, *, path: str, errors: list[ValidationError]) -> None:
    if style is None:
        return
    if isinstance(style, list):
        for i, s in enumerate(style):
            _validate_style(s, path=f"{path}[{i}]", errors=errors)
        return
    if not isinstance(style, dict):
        errors.append(ValidationError(path, "must be an object or array of objects"))
        return
    for k in style:
        if k not in ALLOWED_STYLE_KEYS:
            errors.append(ValidationError(
                f"{path}.{k}",
                f"style key `{k}` not allowed. "
                f"Use RN-style keys like fontSize, padding, flexDirection, backgroundColor.",
            ))


# ── Normalisation ───────────────────────────────────────────────────


def normalise_spec(spec: Any) -> Any:
    """Rewrite common LLM shortcuts into canonical form (non-destructive).

    - ``{root: {...}}`` → unwraps to ``{...}``
    - Top-level ``text``/``title``/``label``/``placeholder``/``icon``/``variant``
      attrs get folded into ``props``
    - Top-level ``style`` folded into ``props.style``
    - Aliases: ``Container``/``Box``/``Stack`` → ``View``; ``HStack`` → ``Row``;
      ``TouchableOpacity``/``Pressable`` → ``Button`` (keeps onPress ignored);
      ``FlatList``/``SectionList`` → ``List``; ``TodoList`` → ``List``;
      ``TodoItem`` → ``ListItem``.

    Does NOT add/remove content — only reshapes so that :func:`validate_spec`
    has a chance to accept it.
    """
    if not isinstance(spec, dict):
        return spec

    # Unwrap {"root": {...}}
    if set(spec.keys()) >= {"root"} and isinstance(spec.get("root"), dict):
        return {"root": _normalise_node(spec["root"])}
    return _normalise_node(spec)


_TYPE_ALIASES = {
    "Container": "View",
    "Box": "View",
    "Stack": "View",
    "Column": "View",
    "SafeAreaView": "View",
    "KeyboardAvoidingView": "View",
    "Page": "Screen",
    "HStack": "Row",
    "HorizontalStack": "Row",
    "Scroll": "ScrollView",
    "TouchableOpacity": "Button",
    "Pressable": "Button",
    "IconButton": "Button",
    "FlatList": "List",
    "SectionList": "List",
    "ItemList": "List",
    "TodoList": "List",
    "TodoItem": "ListItem",
    "RowItem": "ListItem",
    "Navbar": "Appbar",
    "Header": "Appbar",
    "TopBar": "Appbar",
    "TitleBar": "Appbar",
    "Title": "Heading",
    "Label": "Text",
    "Paragraph": "Text",
    "Caption": "Text",
    "FloatingActionButton": "Fab",
    "FloatingButton": "Fab",
    "ColorCard": "Card",
    "Tile": "Card",
    "Chip": "Badge",
}

# Non-style attrs that might appear at the top level of a node — move them to props.
_TOP_LEVEL_PROP_KEYS = {
    "text", "title", "label", "placeholder", "value", "icon", "variant",
    "src", "alt", "name", "multiline", "secureTextEntry", "description",
    "completed", "checked", "active", "disabled", "level", "subtitle",
}

_STRUCTURAL_KEYS = {"type", "props", "children", "style"}


def _normalise_node(node: Any) -> Any:
    if not isinstance(node, dict):
        return node

    out: dict[str, Any] = {}
    raw_type = node.get("type")
    if isinstance(raw_type, str):
        out["type"] = _TYPE_ALIASES.get(raw_type, raw_type)

    # Accumulate props from nested props + top-level keys
    props: dict[str, Any] = {}
    nested = node.get("props")
    if isinstance(nested, dict):
        props.update(nested)
    for k, v in node.items():
        if k in _STRUCTURAL_KEYS:
            continue
        if k == "style":
            continue
        if k in _TOP_LEVEL_PROP_KEYS:
            props.setdefault(k, v)

    # Merge style
    style = node.get("style")
    if style is None and isinstance(nested, dict):
        style = nested.get("style")
    if style is not None:
        props["style"] = style

    if props:
        out["props"] = props

    # Recurse children
    children = node.get("children")
    if isinstance(children, list):
        out["children"] = [_normalise_node(c) if isinstance(c, dict) else c for c in children]
    elif children is not None:
        out["children"] = children

    return out


def _suggest_type(unknown: str) -> str | None:
    """Best-effort suggestion for a misspelled or aliased type."""
    if not unknown:
        return None
    if unknown in _TYPE_ALIASES:
        return _TYPE_ALIASES[unknown]
    lowered = unknown.lower()
    for canonical in ALLOWED_TYPES:
        if canonical.lower() == lowered:
            return canonical
    return None


# ── Prompt snippet (single source of truth shared with Designer skill) ──

CANONICAL_VOCAB_MARKDOWN = """\
### .bsd spec canonical schema

Every screen spec must be a JSON tree of `{type, props, children}` nodes
using ONLY these component types:

- **Layout**:    `Screen` (root), `View`, `Row`, `ScrollView`
- **Text**:      `Text`, `Heading`
- **Input**:     `Button`, `TextInput`, `Checkbox`, `Switch`
- **Display**:   `Image`, `Icon`, `Badge`, `Card`, `Divider`
- **Lists**:     `List`, `ListItem`
- **Navigation**: `Appbar`, `Tab`, `Fab`

Node shape (always this exact shape — never flat props):

```json
{
  "type": "View",
  "props": {
    "style": { "padding": 16, "gap": 12 },
    "<otherProp>": "..."
  },
  "children": [ ... ]
}
```

Root node MUST be `Screen`, `View`, or `ScrollView`.

Allowed **style** keys: flex, flexDirection, justifyContent, alignItems, gap,
padding (+ per-side), margin (+ per-side), width, height, backgroundColor,
color, borderColor, borderWidth, borderRadius, fontSize, fontWeight,
fontFamily, lineHeight, textAlign, opacity, position, top/right/bottom/left.

Allowed per-type props:

- `Text` / `Heading`: `text` (string). `Heading` also accepts `level` (1|2|3).
- `Button`: `text`, `variant` (primary|secondary|danger|ghost), `icon`, `disabled`.
- `TextInput`: `label`, `placeholder`, `value`, `multiline`, `secureTextEntry`.
- `Checkbox` / `Switch`: `label`, `checked` / `value`.
- `Image`: `src`, `alt`.
- `Icon`: `name`.
- `Badge`: `text`, `variant` (neutral|info|success|warning|danger).
- `Card`: `title`, `subtitle`.
- `ListItem`: `text`, `description`, `completed`, `icon`.
- `Appbar`: `title`.
- `Tab`: `label`, `active`.
- `Fab`: `icon`, `label`.

**Example** — simple Todo list screen:

```json
{
  "type": "Screen",
  "props": { "style": { "backgroundColor": "#f9fafb" } },
  "children": [
    { "type": "Appbar", "props": { "title": "My Todos" } },
    {
      "type": "ScrollView",
      "props": { "style": { "padding": 16, "gap": 12 } },
      "children": [
        { "type": "List", "children": [
          { "type": "ListItem", "props": { "text": "Ship v1", "completed": false } },
          { "type": "ListItem", "props": { "text": "Write docs", "completed": true } }
        ] }
      ]
    },
    { "type": "Fab", "props": { "icon": "plus" } }
  ]
}
```

If you use a type or prop outside this list, the spec will be rejected and
you will be asked to fix it.
"""
