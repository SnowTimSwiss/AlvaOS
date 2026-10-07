package org.alvaos.app

import android.content.Context
import android.content.res.ColorStateList
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.core.widget.TextViewCompat
import com.google.android.material.button.MaterialButton
import com.google.android.material.card.MaterialCardView
import com.google.android.material.color.MaterialColors
import com.google.android.material.materialswitch.MaterialSwitch
import com.google.android.material.snackbar.Snackbar
import com.google.android.material.textfield.TextInputEditText
import com.google.android.material.textfield.TextInputLayout
import com.google.android.material.R as M

/**
 * The building blocks of the app's screens, in code and Material 3: pages,
 * cards, text, buttons, fields and switches. Colours come from the theme, so
 * light, dark and the phone's own colours (Android 12+) just work.
 */
class Ui(private val ctx: Context) {
    fun dp(v: Int) = (v * ctx.resources.displayMetrics.density).toInt()
    fun color(attr: Int) = MaterialColors.getColor(ctx, attr, Color.GRAY)

    private val match = ViewGroup.LayoutParams.MATCH_PARENT
    private val wrap = ViewGroup.LayoutParams.WRAP_CONTENT

    /** A scrolling page; returns the column to fill. */
    fun page(into: ViewGroup): LinearLayout {
        val column = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(20), dp(20), dp(20), dp(28))
        }
        into.addView(ScrollView(ctx).apply { isFillViewport = true; addView(column) }, ViewGroup.LayoutParams(match, match))
        return column
    }

    private fun LinearLayout.add(view: View, top: Int = 0, width: Int = match): View {
        addView(view, LinearLayout.LayoutParams(width, wrap).apply { topMargin = dp(top) })
        return view
    }

    private fun text(parent: LinearLayout, value: CharSequence, style: Int, top: Int, colorAttr: Int? = null) =
        TextView(ctx).apply {
            text = value
            TextViewCompat.setTextAppearance(this, style)
            if (colorAttr != null) setTextColor(color(colorAttr))
        }.also { parent.add(it, top) }

    fun headline(parent: LinearLayout, value: String, top: Int = 4) =
        text(parent, value, M.style.TextAppearance_Material3_HeadlineMedium, top, M.attr.colorOnSurface)
    fun title(parent: LinearLayout, value: String, top: Int = 0) =
        text(parent, value, M.style.TextAppearance_Material3_TitleMedium, top, M.attr.colorOnSurface)
    fun body(parent: LinearLayout, value: CharSequence, top: Int = 6) =
        text(parent, value, M.style.TextAppearance_Material3_BodyLarge, top, M.attr.colorOnSurfaceVariant)
    fun caption(parent: LinearLayout, value: CharSequence, top: Int = 4) =
        text(parent, value, M.style.TextAppearance_Material3_BodyMedium, top, M.attr.colorOnSurfaceVariant)
    fun section(parent: LinearLayout, value: String) =
        text(parent, value, M.style.TextAppearance_Material3_LabelLarge, 24, M.attr.colorPrimary)

    /** Empty room of a fixed height (a plain View with wrap_content would take all there is). */
    fun space(parent: LinearLayout, size: Int) =
        parent.addView(View(ctx), LinearLayout.LayoutParams(match, dp(size)))

    /** A line with an icon: what something does, for the first screen. */
    fun feature(parent: LinearLayout, icon: Int, head: String, text: String) {
        val line = row(parent, 18)
        line.gravity = Gravity.TOP
        badge(line, icon, 40)
        val texts = LinearLayout(ctx).apply { orientation = LinearLayout.VERTICAL }
        line.addView(texts, LinearLayout.LayoutParams(0, wrap, 1f).apply { marginStart = dp(14) })
        title(texts, head)
        caption(texts, text, 2)
    }

    /** A tile of a grid: an icon and a name, tapping it does something. */
    fun tile(parent: LinearLayout, icon: Int, name: String, sub: String, onClick: () -> Unit): View {
        val column = card(parent, 0)
        column.gravity = Gravity.CENTER_HORIZONTAL
        badge(column, icon, 52)
        title(column, name, 10).gravity = Gravity.CENTER
        if (sub.isNotEmpty()) caption(column, sub, 0).gravity = Gravity.CENTER
        val card = column.parent as View
        card.isClickable = true
        card.setOnClickListener { onClick() }
        return card
    }

    /** A rounded card in the surface colour; returns its column. */
    fun card(parent: LinearLayout, top: Int = 12, tint: Int = M.attr.colorSurfaceContainerHigh): LinearLayout {
        val column = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(18), dp(16), dp(18), dp(18))
        }
        val card = MaterialCardView(ctx).apply {
            radius = dp(24).toFloat()
            cardElevation = 0f
            strokeWidth = 0
            setCardBackgroundColor(color(tint))
            addView(column)
        }
        parent.add(card, top)
        return column
    }

    /** An icon in a soft circle, as at the top of a card. */
    fun badge(parent: LinearLayout, icon: Int, size: Int = 48, tint: Int = M.attr.colorPrimary,
              fill: Int = M.attr.colorPrimaryContainer): ImageView {
        val view = ImageView(ctx).apply {
            setImageResource(icon)
            imageTintList = ColorStateList.valueOf(color(tint))
            background = GradientDrawable().apply { shape = GradientDrawable.OVAL; setColor(color(fill)) }
            val pad = dp(size) / 4
            setPadding(pad, pad, pad, pad)
        }
        parent.addView(view, LinearLayout.LayoutParams(dp(size), dp(size)))
        return view
    }

    fun logo(parent: LinearLayout, size: Int = 72) = ImageView(ctx).apply {
        setImageResource(R.drawable.ic_logo)
        parent.addView(this, LinearLayout.LayoutParams(dp(size), dp(size)).apply { topMargin = dp(24) })
    }

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
            minHeight = dp(if (kind == Kind.Text) 44 else 52)
            if (icon != 0) { setIconResource(icon); iconGravity = MaterialButton.ICON_GRAVITY_TEXT_START }
            if (kind == Kind.Danger) {
                setTextColor(color(M.attr.colorError))
                strokeColor = ColorStateList.valueOf(color(M.attr.colorError))
            }
            setOnClickListener { onClick() }
        }
        parent.add(b, top)
        return b
    }

    /** Two buttons side by side. */
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

    fun switch(parent: LinearLayout, label: String, on: Boolean, onChange: (Boolean) -> Unit): MaterialSwitch =
        MaterialSwitch(ctx).apply {
            text = label
            isChecked = on
            TextViewCompat.setTextAppearance(this, M.style.TextAppearance_Material3_BodyLarge)
            setTextColor(color(M.attr.colorOnSurface))
            minHeight = dp(52)
            setOnCheckedChangeListener { _, checked -> onChange(checked) }
        }.also { parent.add(it, 4) }

    /** A letter in a circle, for the person. */
    fun avatar(parent: LinearLayout, name: String, size: Int = 52) = TextView(ctx).apply {
        text = name.take(1).uppercase()
        gravity = Gravity.CENTER
        setTypeface(typeface, Typeface.BOLD)
        textSize = 22f
        setTextColor(color(M.attr.colorOnPrimary))
        background = GradientDrawable().apply { shape = GradientDrawable.OVAL; setColor(color(M.attr.colorPrimary)) }
        parent.addView(this, LinearLayout.LayoutParams(dp(size), dp(size)))
    }

    fun error(parent: LinearLayout, message: String) {
        val box = card(parent, 16, M.attr.colorErrorContainer)
        text(box, message, M.style.TextAppearance_Material3_BodyMedium, 0, M.attr.colorOnErrorContainer)
    }
}

/** A short note at the bottom of the screen. */
object Snack {
    fun show(view: View, message: String) = Snackbar.make(view, message, Snackbar.LENGTH_SHORT).show()
}
