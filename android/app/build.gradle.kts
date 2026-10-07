plugins {
    id("com.android.application") version "8.11.1"
    kotlin("android") version "2.0.21"
}

// The version name is the one of AlvaOS (the VERSION file); the version code
// counts up with every build CI makes (Google Play needs a bigger one each time).
val alvaosVersion = System.getenv("ALVAOS_VERSION_NAME")
    ?: rootDir.resolve("../VERSION").takeIf { it.exists() }?.readText()?.trim() ?: "dev"
val buildNumber = System.getenv("ALVAOS_VERSION_CODE")?.toIntOrNull() ?: 1

// The upload key for Google Play, only from the environment (CI secrets),
// never in the repository. Without it a release build is not signed.
val keystore = System.getenv("ALVAOS_KEYSTORE")?.let { file(it) }?.takeIf { it.exists() }

android {
    namespace = "org.alvaos.app"
    compileSdk = 36

    defaultConfig {
        applicationId = "uk.timserver.alvaos"     // on Google Play; never changes after the first upload
        minSdk = 30            // Android 11: deleting pictures through MediaStore's own request
        targetSdk = 36         // what Google Play asks of new apps and updates
        versionCode = buildNumber
        versionName = alvaosVersion
    }
    signingConfigs {
        if (keystore != null) {
            create("upload") {
                storeFile = keystore
                storePassword = System.getenv("ALVAOS_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("ALVAOS_KEY_ALIAS")
                keyPassword = System.getenv("ALVAOS_KEY_PASSWORD")
            }
        }
    }
    buildTypes {
        release {
            isMinifyEnabled = false
            if (keystore != null) signingConfig = signingConfigs.getByName("upload")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
    lint { abortOnError = false }
    // Every screen opened on the JVM (Robolectric) with pictures of it (Roborazzi):
    // app/build/outputs/roborazzi/*.png, kept by CI.
    testOptions {
        unitTests {
            isIncludeAndroidResources = true
            all {
                it.systemProperty("roborazzi.test.record", "true")
                it.testLogging {
                    events("failed")
                    exceptionFormat = org.gradle.api.tasks.testing.logging.TestExceptionFormat.FULL
                }
            }
        }
    }
}

dependencies {
    implementation(project(":core"))
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("com.google.android.material:material:1.12.0")
    implementation("androidx.work:work-runtime-ktx:2.9.1")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.6")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")
    // Google's QR scanner screen (Play services): no camera permission for the app.
    implementation("com.google.android.gms:play-services-code-scanner:16.1.0")

    testImplementation("junit:junit:4.13.2")
    testImplementation("org.robolectric:robolectric:4.14.1")
    testImplementation("androidx.test:core:1.6.1")
    testImplementation("androidx.test.ext:junit:1.2.1")
    testImplementation("androidx.work:work-testing:2.9.1")
    testImplementation("io.github.takahirom.roborazzi:roborazzi:1.40.1")
}
