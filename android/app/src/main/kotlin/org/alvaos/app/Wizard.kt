package org.alvaos.app

import android.animation.ObjectAnimator
import android.animation.ValueAnimator
import android.content.Context
import android.content.res.ColorStateList
import android.content.res.Configuration
import android.graphics.BlurMaskFilter
import android.graphics.Canvas
import android.graphics.DashPathEffect
import android.graphics.Paint
import android.graphics.Path
import android.graphics.PathMeasure
import android.graphics.RectF
import android.graphics.SweepGradient
import android.graphics.drawable.GradientDrawable
import android.os.SystemClock
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.animation.AccelerateDecelerateInterpolator
import android.view.animation.DecelerateInterpolator
import android.view.animation.LinearInterpolator
import android.view.animation.OvershootInterpolator
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import androidx.core.content.ContextCompat
import androidx.core.graphics.ColorUtils
import com.google.android.material.card.MaterialCardView
import kotlin.math.PI
import kotlin.math.min
import kotlin.math.sin
import androidx.appcompat.R as AC
import com.google.android.material.R as M

/** Movement in the setup: soft, short, and off when the phone says "remove animations". */
object Motion {
    /** The tests of the screens switch it off (a picture of a half-faded screen helps nobody). */
    @JvmField var enabled = true

    fun on() = enabled && ValueAnimator.areAnimatorsEnabled()

    /** The children of [parent] come in one after another, rising a little. */
    fun stagger(parent: ViewGroup, from: Int = 0, step: Long = 70) {
        if (!on()) return
        val rise = 14 * parent.resources.displayMetrics.density
        for (i in from until parent.childCount) {
            val view = parent.getChildAt(i)
            view.alpha = 0f
            view.translationY = rise
            view.animate().alpha(1f).translationY(0f)
                .setStartDelay(60L + (i - from) * step).setDuration(450)
                .setInterpolator(DecelerateInterpolator(2f)).start()
        }
    }

    internal fun easeOutBack(t: Float): Float {
        val x = t.coerceIn(0f, 1f) - 1f
        return 1f + 2.70158f * x * x * x + 1.70158f * x * x
    }

    internal fun easeOut(t: Float): Float {
        val x = 1f - t.coerceIn(0f, 1f)
        return 1f - x * x * x
    }
}

private fun Context.night() = (resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK) == Configuration.UI_MODE_NIGHT_YES
private fun Context.green() = if (night()) 0xFF4CC38A.toInt() else 0xFF1D8A4E.toInt()

/** The logo with a slowly turning, soft glow behind it, and a small float. */
class HaloLogo(ctx: Context) : FrameLayout(ctx) {
    private val ui = Ui(ctx)
    private val halo = object : View(ctx) {
        private val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            maskFilter = BlurMaskFilter(ui.dp(16).toFloat(), BlurMaskFilter.Blur.NORMAL)
            alpha = 115
        }
        override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
            paint.shader = SweepGradient(w / 2f, h / 2f, intArrayOf(
                ui.color(AC.attr.colorPrimary), ui.color(M.attr.colorTertiary), ui.color(M.attr.colorSecondary),
                ui.color(AC.attr.colorPrimary)), null)
        }
        override fun onDraw(canvas: Canvas) {
            val m = ui.dp(20).toFloat()
            canvas.drawRoundRect(RectF(m, m, width - m, height - m), width * 0.2f, width * 0.2f, paint)
        }
    }
    private val logo = ImageView(ctx).apply { setImageResource(R.drawable.ic_logo) }
    private val running = mutableListOf<android.animation.Animator>()

    init {
        clipChildren = false
        halo.setLayerType(View.LAYER_TYPE_SOFTWARE, null)
        addView(halo, FrameLayout.LayoutParams(ui.dp(128), ui.dp(128), Gravity.CENTER))
        addView(logo, FrameLayout.LayoutParams(ui.dp(88), ui.dp(88), Gravity.CENTER))
    }

    override fun onAttachedToWindow() {
        super.onAttachedToWindow()
        if (!Motion.on()) return
        running += ObjectAnimator.ofFloat(halo, View.ROTATION, 0f, 360f).apply {
            duration = 9000; repeatCount = ValueAnimator.INFINITE; interpolator = LinearInterpolator(); start()
        }
        running += ObjectAnimator.ofFloat(logo, View.TRANSLATION_Y, 0f, -ui.dp(6).toFloat()).apply {
            duration = 3000; repeatCount = ValueAnimator.INFINITE; repeatMode = ValueAnimator.REVERSE
            interpolator = AccelerateDecelerateInterpolator(); start()
        }
        logo.scaleX = 0.5f; logo.scaleY = 0.5f; logo.alpha = 0f
        logo.animate().scaleX(1f).scaleY(1f).alpha(1f).setDuration(650).setInterpolator(OvershootInterpolator(1.6f)).start()
    }

    override fun onDetachedFromWindow() {
        running.forEach { it.cancel() }
        running.clear()
        super.onDetachedFromWindow()
    }
}

/**
 * The phone and the NAS with a dashed line between them: little lights travel along it (faster
 * while connecting), a lock in the middle says it is private. Done: the line closes in green.
 * Failed: the NAS shakes.
 */
class Bridge(ctx: Context) : View(ctx) {
    enum class State { Idle, Busy, Done, Fail }

    private val ui = Ui(ctx)
    private var state = State.Idle
    private var since = 0L
    private var changed = 0L
    private val fill = Paint(Paint.ANTI_ALIAS_FLAG)
    private val line = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE; strokeWidth = ui.dp(2).toFloat() }
    private val dot = Paint(Paint.ANTI_ALIAS_FLAG)
    private val text = Paint(Paint.ANTI_ALIAS_FLAG).apply { textAlign = Paint.Align.CENTER; textSize = ui.dp(12).toFloat() }
    private val phone = ContextCompat.getDrawable(ctx, R.drawable.ic_phone)!!.mutate()
    private val nas = ContextCompat.getDrawable(ctx, R.drawable.ic_nas)!!.mutate()
    private val lock = ContextCompat.getDrawable(ctx, R.drawable.ic_lock)!!.mutate()

    fun set(next: State) {
        state = next
        changed = SystemClock.uptimeMillis()
        invalidate()
    }

    override fun onMeasure(widthSpec: Int, heightSpec: Int) =
        setMeasuredDimension(View.MeasureSpec.getSize(widthSpec), ui.dp(116))

    override fun onAttachedToWindow() {
        super.onAttachedToWindow()
        since = SystemClock.uptimeMillis()
        changed = since
    }

    private fun box(canvas: Canvas, cx: Float, top: Float, icon: android.graphics.drawable.Drawable, tint: Int, scale: Float,
                    label: String, dx: Float = 0f) {
        val half = ui.dp(28).toFloat()
        canvas.save()
        canvas.translate(dx, 0f)
        canvas.scale(scale, scale, cx, top + half)
        val r = RectF(cx - half, top, cx + half, top + 2 * half)
        fill.style = Paint.Style.FILL
        fill.color = ui.color(M.attr.colorSurfaceContainerHigh)
        canvas.drawRoundRect(r, ui.dp(16).toFloat(), ui.dp(16).toFloat(), fill)
        fill.style = Paint.Style.STROKE
        fill.strokeWidth = ui.dp(1).toFloat()
        fill.color = ui.color(M.attr.colorOutlineVariant)
        canvas.drawRoundRect(r, ui.dp(16).toFloat(), ui.dp(16).toFloat(), fill)
        icon.setTint(tint)
        val pad = ui.dp(14)
        icon.setBounds((r.left + pad).toInt(), (r.top + pad).toInt(), (r.right - pad).toInt(), (r.bottom - pad).toInt())
        icon.draw(canvas)
        canvas.restore()
        text.color = ui.color(M.attr.colorOnSurfaceVariant)
        canvas.drawText(label, cx + dx, top + 2 * half + ui.dp(18), text)
    }

    override fun onDraw(canvas: Canvas) {
        val moving = Motion.on()
        val now = SystemClock.uptimeMillis()
        val t = if (moving) now - since else 4000L
        val span = min(width, ui.dp(340)).toFloat()
        val x0 = (width - span) / 2f
        val half = ui.dp(28).toFloat()
        val top = ui.dp(8).toFloat()
        val left = x0 + ui.dp(14) + half
        val right = x0 + span - ui.dp(14) - half
        val y = top + half
        val green = context.green()
        val accent = ui.color(AC.attr.colorPrimary)
        val second = ui.color(M.attr.colorTertiary)

        val shake = if (state == State.Fail && moving) {
            val q = ((now - changed) / 450f).coerceIn(0f, 1f)
            sin(q * 4 * PI).toFloat() * ui.dp(5) * (1f - q)
        } else 0f
        box(canvas, left, top, phone, accent, Motion.easeOutBack(t / 600f), "This phone")
        box(canvas, right, top, nas, second, Motion.easeOutBack((t - 150) / 600f), "Your NAS", shake)

        // The line, and the lights on it
        val a = left + half + ui.dp(8)
        val b = right - half - ui.dp(8)
        line.color = when (state) {
            State.Done -> green
            State.Fail -> ui.color(AC.attr.colorError)
            else -> ColorUtils.setAlphaComponent(accent, 110)
        }
        line.pathEffect = if (state == State.Done) null else DashPathEffect(floatArrayOf(ui.dp(5).toFloat(), ui.dp(5).toFloat()), 0f)
        canvas.drawLine(a, y, b, y, line)
        if (state != State.Fail) {
            val period = if (state == State.Busy) 900f else 2400f
            for (k in 0..2) {
                var p = ((t / period) + k / 3f) % 1f
                if (k == 1) p = 1f - p
                val alpha = sin(p * PI).toFloat()
                val color = if (state == State.Done) green else if (k == 1) second else accent
                dot.color = ColorUtils.setAlphaComponent(color, (60 * alpha).toInt())
                canvas.drawCircle(a + (b - a) * p, y, ui.dp(8).toFloat(), dot)
                dot.color = ColorUtils.setAlphaComponent(color, (255 * alpha).toInt())
                canvas.drawCircle(a + (b - a) * p, y, ui.dp(4).toFloat(), dot)
            }
        }
        // The lock
        val cx = (a + b) / 2f
        fill.style = Paint.Style.FILL
        fill.color = ui.color(M.attr.colorSurface)
        canvas.drawCircle(cx, y, ui.dp(15).toFloat(), fill)
        fill.style = Paint.Style.STROKE
        fill.color = ui.color(M.attr.colorOutlineVariant)
        canvas.drawCircle(cx, y, ui.dp(15).toFloat(), fill)
        lock.setTint(if (state == State.Fail) ui.color(AC.attr.colorError) else green)
        val l = ui.dp(8)
        lock.setBounds((cx - l).toInt(), (y - l).toInt(), (cx + l).toInt(), (y + l).toInt())
        lock.draw(canvas)
        if (moving && isAttachedToWindow) postInvalidateOnAnimation()
    }
}

/** A circle that draws itself, then a check mark; rings spread from it. */
class CheckBurst(ctx: Context, private val size: Int, private val rings: Boolean) : View(ctx) {
    private val ui = Ui(ctx)
    private var since = 0L
    private val paint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val mark = Path()

    override fun onMeasure(widthSpec: Int, heightSpec: Int) = setMeasuredDimension(ui.dp(size), ui.dp(size))

    override fun onAttachedToWindow() {
        super.onAttachedToWindow()
        since = SystemClock.uptimeMillis()
    }

    override fun onDraw(canvas: Canvas) {
        val moving = Motion.on()
        val t = if (moving) (SystemClock.uptimeMillis() - since).toFloat() else 5000f
        val green = context.green()
        val cx = width / 2f
        val cy = height / 2f
        val r = width * (if (rings) 0.30f else 0.44f)

        if (rings && t > 900f) for (i in 0..2) {
            val q = (((t - 900f) / 2400f) + i / 3f) % 1f
            paint.style = Paint.Style.STROKE
            paint.strokeWidth = ui.dp(2).toFloat()
            paint.color = ColorUtils.setAlphaComponent(green, (180 * (1f - q)).toInt())
            canvas.drawCircle(cx, cy, r * (0.9f + 0.8f * q), paint)
        }
        paint.style = Paint.Style.FILL
        paint.color = ColorUtils.setAlphaComponent(green, 36)
        canvas.drawCircle(cx, cy, r * Motion.easeOutBack(t / 450f), paint)

        paint.style = Paint.Style.STROKE
        paint.strokeWidth = ui.dp(3).toFloat()
        paint.strokeCap = Paint.Cap.ROUND
        paint.strokeJoin = Paint.Join.ROUND
        paint.color = green
        canvas.drawArc(RectF(cx - r, cy - r, cx + r, cy + r), -90f, 360f * Motion.easeOut(t / 700f), false, paint)

        val k = r / 23f
        val full = Path().apply {
            moveTo(cx + (15 - 26) * k, cy + (27 - 26) * k)
            lineTo(cx + (22 - 26) * k, cy + (34 - 26) * k)
            lineTo(cx + (37 - 26) * k, cy + (18 - 26) * k)
        }
        val measure = PathMeasure(full, false)
        mark.reset()
        measure.getSegment(0f, measure.length * Motion.easeOut((t - 650f) / 450f), mark, true)
        paint.strokeWidth = ui.dp(4).toFloat()
        canvas.drawPath(mark, paint)
        if (moving && isAttachedToWindow) postInvalidateOnAnimation()
    }
}

/** The pieces of the setup screens that the rest of the app has no use for. */
class Wizard(private val ctx: Context, private val ui: Ui) {
    /** Three little bars, the ones up to [step] filled: where in the setup we are. */
    fun steps(parent: LinearLayout, step: Int) {
        val row = LinearLayout(ctx).apply { gravity = Gravity.CENTER; orientation = LinearLayout.HORIZONTAL }
        for (i in 0..2) {
            val on = i <= step
            val width = if (i == step) 34 else 22
            row.addView(View(ctx).apply {
                background = GradientDrawable().apply {
                    cornerRadius = ui.dp(3).toFloat()
                    setColor(ui.color(if (on) AC.attr.colorPrimary else M.attr.colorOutlineVariant))
                }
            }, LinearLayout.LayoutParams(ui.dp(width), ui.dp(5)).apply { marginStart = ui.dp(3); marginEnd = ui.dp(3) })
        }
        parent.addView(row, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
    }

    fun halo(parent: LinearLayout) = parent.addView(HaloLogo(ctx), LinearLayout.LayoutParams(ui.dp(128), ui.dp(128)).apply {
        gravity = Gravity.CENTER_HORIZONTAL; topMargin = ui.dp(24)
    })

    fun bridge(parent: LinearLayout, top: Int = 16): Bridge = Bridge(ctx).also {
        parent.addView(it, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
            .apply { topMargin = ui.dp(top) })
    }

    fun burst(parent: LinearLayout, size: Int, rings: Boolean) = parent.addView(CheckBurst(ctx, size, rings),
        LinearLayout.LayoutParams(ui.dp(size), ui.dp(size)).apply { gravity = Gravity.CENTER_HORIZONTAL; topMargin = ui.dp(8) })

    /**
     * A card to switch on or off, with a round check at the end: blue and ticked when it is on.
     * [onChange] gets the new state; [enabled] false dims the card and ignores taps.
     */
    class Choice(val card: MaterialCardView, private val show: (Boolean) -> Unit) {
        var checked = false
            private set
        fun set(on: Boolean, animate: Boolean = true) { checked = on; show(on) }
        fun enable(on: Boolean) { card.isEnabled = on; card.alpha = if (on) 1f else 0.5f }
    }

    fun choice(parent: LinearLayout, icon: Int, title: String, sub: String, on: Boolean, onChange: (Boolean) -> Unit): Choice {
        val column = ui.card(parent, 8, padding = 14)
        val card = column.parent as MaterialCardView
        val row = ui.row(column, 0)
        val badge = ui.badge(row, icon, 40)
        val texts = LinearLayout(ctx).apply { orientation = LinearLayout.VERTICAL }
        row.addView(texts, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply {
            marginStart = ui.dp(14); marginEnd = ui.dp(10)
        })
        ui.title(texts, title)
        ui.caption(texts, sub, 2)
        val tick = ImageView(ctx).apply {
            setImageResource(R.drawable.ic_check)
            imageTintList = ColorStateList.valueOf(ui.color(M.attr.colorOnPrimary))
            setPadding(ui.dp(3), ui.dp(3), ui.dp(3), ui.dp(3))
        }
        row.addView(tick, LinearLayout.LayoutParams(ui.dp(24), ui.dp(24)))
        val choice = Choice(card) { checked ->
            val accent = ui.color(AC.attr.colorPrimary)
            card.strokeColor = if (checked) ColorUtils.blendARGB(ui.color(M.attr.colorOutlineVariant), accent, 0.55f) else ui.color(M.attr.colorOutlineVariant)
            card.setCardBackgroundColor(if (checked) ColorUtils.blendARGB(ui.color(M.attr.colorSurfaceContainerHigh), accent, 0.06f)
                else ui.color(M.attr.colorSurfaceContainerHigh))
            badge.imageTintList = ColorStateList.valueOf(ui.color(if (checked) AC.attr.colorPrimary else M.attr.colorOutline))
            tick.background = GradientDrawable().apply {
                shape = GradientDrawable.OVAL
                if (checked) setColor(accent) else setStroke(ui.dp(2), ui.color(M.attr.colorOutlineVariant))
            }
            if (Motion.on()) {
                tick.animate().cancel()
                tick.scaleX = 0.6f; tick.scaleY = 0.6f
                tick.animate().scaleX(1f).scaleY(1f).setDuration(250).setInterpolator(OvershootInterpolator(2.5f)).start()
            }
            tick.imageAlpha = if (checked) 255 else 0
        }
        choice.set(on, animate = false)
        card.setOnClickListener {
            if (!card.isEnabled) return@setOnClickListener
            choice.set(!choice.checked)
            onChange(choice.checked)
        }
        return choice
    }

    /** A line of the summary: a green check and what was done. */
    fun done(parent: LinearLayout, text: String) {
        val row = ui.row(parent, 8)
        row.setPadding(ui.dp(14), ui.dp(10), ui.dp(14), ui.dp(10))
        row.background = GradientDrawable().apply {
            cornerRadius = ui.dp(12).toFloat()
            setColor(ui.color(M.attr.colorSurfaceContainerHigh))
            setStroke(ui.dp(1), ui.color(M.attr.colorOutlineVariant))
        }
        row.addView(ImageView(ctx).apply {
            setImageResource(R.drawable.ic_check)
            imageTintList = ColorStateList.valueOf(ctx.green())
        }, LinearLayout.LayoutParams(ui.dp(20), ui.dp(20)))
        row.addView(android.widget.TextView(ctx).apply {
            this.text = text
            textSize = 14.5f
            setTextColor(ui.color(M.attr.colorOnSurface))
        }, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply { marginStart = ui.dp(12) })
    }
}
