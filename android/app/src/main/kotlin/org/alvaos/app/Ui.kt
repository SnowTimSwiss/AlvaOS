package org.alvaos.app

import android.content.Context
import android.content.res.ColorStateList
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.text.InputType
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.ImageButton
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import com.google.android.material.button.MaterialButton
import com.google.android.material.card.MaterialCardView
import com.google.android.material.checkbox.MaterialCheckBox
import com.google.android.material.color.MaterialColors
import com.google.android.material.materialswitch.MaterialSwitch
import com.google.android.material.progressindicator.LinearProgressIndicator
import com.google.android.material.snackbar.Snackbar
import com.google.android.material.textfield.TextInputEditText
import com.google.android.material.textfield.TextInputLayout
import com.google.android.material.R as M

/**
 * The building blocks of the app's native screens, in code and Material 3, in the look of the
 * Hub (same colours, 14dp cards with a thin line, 10dp buttons, the same line icons), so a
 * native screen and the Hub inside the app read as one app. Colours come from the theme.
 */
class Ui(private val ctx: Context) {
    fun dp(v: Int) = (v * ctx.resources.displayMetrics.density).toInt()
    fun color(attr: Int) = MaterialColors.getColor(ctx, attr, Color.GRAY)

    private val match = ViewGroup.LayoutParams.MATCH_PARENT
    private val wrap = ViewGroup.LayoutParams.WRAP_CONTENT

    private fun LinearLayout.add(view: View, top: Int = 0, width: Int = match): View {
        addView(view, LinearLayout.LayoutParams(width, wrap).apply { topMargin = dp(top) })
        return view
    }

    // ── Screens ──────────────────────────────────────────────────────────

    /** A native screen: the top bar (as the Hub's), then a scrolling column to fill. */
    fun screen(into: ViewGroup, title: String, back: (() -> Unit)? = null,
               actions: List<Action> = emptyList()): LinearLayout {
        val outer = LinearLayout(ctx).apply { orientation = LinearLayout.VERTICAL }
        outer.addView(topBar(title, back, actions), LinearLayout.LayoutParams(match, wrap))
        val column = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(16), dp(14), dp(16), dp(24))
        }
        outer.addView(ScrollView(ctx).apply { isFillViewport = true; addView(column) },
            LinearLayout.LayoutParams(match, 0, 1f))
        into.addView(outer, ViewGroup.LayoutParams(match, match))
        return column
    }

    /** A page without a top bar (signing in). */
    fun page(into: ViewGroup): LinearLayout {
        val column = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(24), dp(20), dp(24), dp(28))
        }
        into.addView(ScrollView(ctx).apply { isFillViewport = true; addView(column) }, ViewGroup.LayoutParams(match, match))
        return column
    }

    class Action(val icon: Int, val label: String, val onClick: () -> Unit)

    /** 57dp high on the panel colour with a thin line below, like the Hub's own top bar. */
    private fun topBar(title: String, back: (() -> Unit)?, actions: List<Action>): View {
        val bar = LinearLayout(ctx).apply { orientation = LinearLayout.VERTICAL }
        val row = LinearLayout(ctx).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            minimumHeight = dp(57)
            setBackgroundColor(color(M.attr.colorSurfaceContainerHigh))
            setPadding(dp(if (back != null) 6 else 16), 0, dp(6), 0)
        }
        if (back != null) row.addView(iconButton(R.drawable.ic_back, "Back", back))
        row.addView(TextView(ctx).apply {
            text = title
            textSize = 17f
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(color(M.attr.colorOnSurface))
            maxLines = 1
            ellipsize = android.text.TextUtils.TruncateAt.END
        }, LinearLayout.LayoutParams(0, wrap, 1f).apply { marginStart = dp(if (back != null) 4 else 0) })
        actions.forEach { row.addView(iconButton(it.icon, it.label, it.onClick)) }
        bar.addView(row, LinearLayout.LayoutParams(match, wrap))
        bar.addView(View(ctx).apply { setBackgroundColor(color(M.attr.colorOutlineVariant)) },
            LinearLayout.LayoutParams(match, dp(1)))
        return bar
    }

    fun iconButton(icon: Int, label: String, onClick: () -> Unit) = ImageButton(ctx).apply {
        setImageResource(icon)
        imageTintList = ColorStateList.valueOf(color(M.attr.colorOnSurfaceVariant))
        contentDescription = label
        val out = TypedValue()
        ctx.theme.resolveAttribute(android.R.attr.selectableItemBackgroundBorderless, out, true)
        setBackgroundResource(out.resourceId)
        layoutParams = LinearLayout.LayoutParams(dp(44), dp(44))
        setOnClickListener { onClick() }
    }

    // ── Text ─────────────────────────────────────────────────────────────

    private fun text(parent: LinearLayout, value: CharSequence, size: Float, bold: Boolean, top: Int, attr: Int) =
        TextView(ctx).apply {
            text = value
            textSize = size
            if (bold) setTypeface(typeface, Typeface.BOLD)
            setTextColor(color(attr))
        }.also { parent.add(it, top) }

    fun headline(parent: LinearLayout, value: String, top: Int = 4) =
        text(parent, value, 24f, true, top, M.attr.colorOnSurface)
    fun title(parent: LinearLayout, value: String, top: Int = 0) =
        text(parent, value, 16f, true, top, M.attr.colorOnSurface)
    fun body(parent: LinearLayout, value: CharSequence, top: Int = 6) =
        text(parent, value, 15f, false, top, M.attr.colorOnSurfaceVariant)
    fun caption(parent: LinearLayout, value: CharSequence, top: Int = 4) =
        text(parent, value, 13f, false, top, M.attr.colorOnSurfaceVariant)
    /** A small heading over a card, like a group in a settings list. */
    fun section(parent: LinearLayout, value: String) =
        text(parent, value, 13f, true, 22, M.attr.colorOnSurfaceVariant).apply { setPadding(dp(4), 0, 0, dp(2)) }

    /** Empty room of a fixed height (a plain View with wrap_content would take all there is). */
    fun space(parent: LinearLayout, size: Int) =
        parent.addView(View(ctx), LinearLayout.LayoutParams(match, dp(size)))

    // ── Cards and rows ───────────────────────────────────────────────────

    /** A card like the Hub's panels: the panel colour, a thin line, 14dp corners. Returns its column. */
    fun card(parent: LinearLayout, top: Int = 12, tint: Int = M.attr.colorSurfaceContainerHigh,
             line: Boolean = true, padding: Int = 16): LinearLayout {
        val column = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(padding), dp(padding), dp(padding), dp(padding))
        }
        val card = MaterialCardView(ctx).apply {
            radius = dp(14).toFloat()
            cardElevation = 0f
            strokeWidth = if (line) dp(1) else 0
            strokeColor = color(M.attr.colorOutlineVariant)
            setCardBackgroundColor(color(tint))
            addView(column)
        }
        parent.add(card, top)
        return column
    }

    /** A card of rows (a settings group): each row ends in a thin line, the last one does not. */
    fun group(parent: LinearLayout, top: Int = 6) = card(parent, top, padding = 0)

    private fun divider(parent: LinearLayout) = parent.addView(
        View(ctx).apply { setBackgroundColor(color(M.attr.colorOutlineVariant)) },
        LinearLayout.LayoutParams(match, dp(1)).apply { marginStart = dp(68) })

    /** A rounded square with an icon in it, as in the Hub's lists. */
    fun badge(parent: LinearLayout, icon: Int, size: Int = 40, tint: Int = M.attr.colorPrimary,
              fill: Int = M.attr.colorPrimaryContainer): ImageView {
        val view = ImageView(ctx).apply {
            setImageResource(icon)
            imageTintList = ColorStateList.valueOf(color(tint))
            background = GradientDrawable().apply { cornerRadius = dp(10).toFloat(); setColor(color(fill)) }
            val pad = dp(size) / 4
            setPadding(pad, pad, pad, pad)
        }
        parent.addView(view, LinearLayout.LayoutParams(dp(size), dp(size)))
        return view
    }

    /** One row of a group: a badge, a title and a line below it, and an arrow if it opens something. */
    fun item(group: LinearLayout, icon: Int, title: String, sub: String = "", onClick: (() -> Unit)? = null,
             danger: Boolean = false): LinearLayout {
        if (group.childCount > 0) divider(group)
        val row = LinearLayout(ctx).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            minimumHeight = dp(64)
            setPadding(dp(16), dp(10), dp(12), dp(10))
        }
        if (danger) badge(row, icon, 40, M.attr.colorError, M.attr.colorErrorContainer) else badge(row, icon)
        val texts = LinearLayout(ctx).apply { orientation = LinearLayout.VERTICAL }
        row.addView(texts, LinearLayout.LayoutParams(0, wrap, 1f).apply { marginStart = dp(14) })
        TextView(ctx).apply {
            text = title
            textSize = 15.5f
            setTextColor(color(if (danger) M.attr.colorError else M.attr.colorOnSurface))
        }.also { texts.add(it) }
        if (sub.isNotEmpty()) caption(texts, sub, 1)
        if (onClick != null && !danger) {
            row.addView(ImageView(ctx).apply {
                setImageResource(R.drawable.ic_chevron)
                imageTintList = ColorStateList.valueOf(color(M.attr.colorOutline))
            }, LinearLayout.LayoutParams(dp(20), dp(20)))
        }
        if (onClick != null) {
            val out = TypedValue()
            ctx.theme.resolveAttribute(android.R.attr.selectableItemBackground, out, true)
            row.setBackgroundResource(out.resourceId)
            row.setOnClickListener { onClick() }
        }
        group.addView(row, LinearLayout.LayoutParams(match, wrap))
        return row
    }

    /** A row with a switch at the end. */
    fun switchItem(group: LinearLayout, icon: Int, title: String, sub: String, on: Boolean,
                   onChange: (Boolean) -> Unit): MaterialSwitch {
        val row = item(group, icon, title, sub)
        val toggle = MaterialSwitch(ctx).apply {
            isChecked = on
            setOnCheckedChangeListener { _, checked -> onChange(checked) }
        }
        row.addView(toggle, LinearLayout.LayoutParams(wrap, wrap))
        row.setOnClickListener { toggle.toggle() }
        return toggle
    }

    /** A row with a checkbox at the end. */
    fun checkItem(group: LinearLayout, icon: Int, title: String, sub: String, checked: Boolean,
                  onChange: (Boolean) -> Unit): MaterialCheckBox {
        val row = item(group, icon, title, sub)
        val box = MaterialCheckBox(ctx).apply {
            isChecked = checked
            setOnCheckedChangeListener { _, on -> onChange(on) }
        }
        row.addView(box, LinearLayout.LayoutParams(wrap, wrap))
        row.setOnClickListener { box.toggle() }
        return box
    }

    /** A thin bar, like the Hub's: the accent on the line colour. */
    fun progress(parent: LinearLayout, done: Int, total: Int, top: Int = 10, indeterminate: Boolean = false) =
        LinearProgressIndicator(ctx).apply {
            this.isIndeterminate = indeterminate
            max = maxOf(1, total)
            setProgressCompat(done, false)
            trackThickness = dp(6)
            trackCornerRadius = dp(3)
            setIndicatorColor(color(M.attr.colorPrimary))
            trackColor = color(M.attr.colorOutlineVariant)
        }.also { parent.add(it, top) }

    // ── The first screen ─────────────────────────────────────────────────

    fun logo(parent: LinearLayout, size: Int = 72) = ImageView(ctx).apply {
        setImageResource(R.drawable.ic_logo)
        parent.addView(this, LinearLayout.LayoutParams(dp(size), dp(size)).apply {
            topMargin = dp(16); gravity = Gravity.CENTER_HORIZONTAL
        })
    }

    /** A line with an icon: what something does, for the first screen. */
    fun feature(parent: LinearLayout, icon: Int, head: String, text: String) {
        val line = row(parent, 16)
        line.gravity = Gravity.TOP
        badge(line, icon, 40)
        val texts = LinearLayout(ctx).apply { orientation = LinearLayout.VERTICAL }
        line.addView(texts, LinearLayout.LayoutParams(0, wrap, 1f).apply { marginStart = dp(14) })
        title(texts, head)
        caption(texts, text, 2)
    }

    // ── Buttons and fields ───────────────────────────────────────────────

    enum class Kind { Filled, Outlined, Text, Danger }

    fun button(parent: LinearLayout, label: String, kind: Kind = Kind.Filled, icon: Int = 0, top: Int = 10,
               onClick: () -> Unit): MaterialButton {
        val attr = when (kind) {
            Kind.Filled -> M.attr.materialButtonStyle
            Kind.Outlined, Kind.Danger -> M.attr.materialButtonOutlinedStyle
            Kind.Text -> M.attr.borderlessButtonStyle
        }
        val b = MaterialButton(ctx, null, attr).apply {
            text = label
            isAllCaps = false
            textSize = 15f
            minHeight = dp(if (kind == Kind.Text) 44 else 50)
            shapeAppearanceModel = shapeAppearanceModel.toBuilder().setAllCornerSizes(dp(10).toFloat()).build()
            if (icon != 0) { setIconResource(icon); iconGravity = MaterialButton.ICON_GRAVITY_TEXT_START }
            if (kind == Kind.Outlined) strokeColor = ColorStateList.valueOf(color(M.attr.colorOutlineVariant))
            if (kind == Kind.Danger) {
                setTextColor(color(M.attr.colorError))
                strokeColor = ColorStateList.valueOf(color(M.attr.colorError))
            }
            setOnClickListener { onClick() }
        }
        parent.add(b, top)
        return b
    }

    /** A line of views side by side. */
    fun row(parent: LinearLayout, top: Int = 10): LinearLayout = LinearLayout(ctx).apply {
        orientation = LinearLayout.HORIZONTAL
        gravity = Gravity.CENTER_VERTICAL
    }.also { parent.add(it, top) }

    fun weighted(view: View, gap: Int = 0) {
        view.layoutParams = LinearLayout.LayoutParams(0, wrap, 1f).apply { marginStart = dp(gap) }
    }

    fun field(parent: LinearLayout, hint: String, value: String = "", kind: Int = InputType.TYPE_CLASS_TEXT,
              help: String = ""): TextInputEditText {
        val layout = TextInputLayout(ctx).apply {
            this.hint = hint
            if (help.isNotEmpty()) helperText = help
            boxBackgroundMode = TextInputLayout.BOX_BACKGROUND_OUTLINE
            setBoxCornerRadii(dp(10).toFloat(), dp(10).toFloat(), dp(10).toFloat(), dp(10).toFloat())
            if (kind and InputType.TYPE_TEXT_VARIATION_PASSWORD != 0) endIconMode = TextInputLayout.END_ICON_PASSWORD_TOGGLE
        }
        val edit = TextInputEditText(layout.context).apply {
            setText(value)
            inputType = kind
            setSingleLine()
        }
        layout.addView(edit)
        parent.add(layout, 12)
        return edit
    }

    fun error(parent: LinearLayout, message: String) {
        val box = card(parent, 16, M.attr.colorErrorContainer, line = false)
        text(box, message, 14f, false, 0, M.attr.colorOnErrorContainer)
    }

    /** A letter in a circle, for the person. */
    fun avatar(parent: LinearLayout, name: String, size: Int = 48) = TextView(ctx).apply {
        text = name.take(1).uppercase()
        gravity = Gravity.CENTER
        setTypeface(typeface, Typeface.BOLD)
        textSize = 20f
        setTextColor(color(M.attr.colorOnPrimary))
        background = GradientDrawable().apply { shape = GradientDrawable.OVAL; setColor(color(M.attr.colorPrimary)) }
        parent.addView(this, LinearLayout.LayoutParams(dp(size), dp(size)))
    }
}

/** A short note at the bottom of the screen. */
object Snack {
    fun show(view: View, message: String) = Snackbar.make(view, message, Snackbar.LENGTH_SHORT).show()
}
