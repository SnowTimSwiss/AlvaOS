package org.alvaos.app

import android.app.Application
import com.google.android.material.color.DynamicColors

/** The app: on Android 12+ its colours follow the phone's wallpaper (Material You). */
class AlvaApp : Application() {
    override fun onCreate() {
        super.onCreate()
        DynamicColors.applyToActivitiesIfAvailable(this)
    }
}
