plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "ai.genie.node"
    compileSdk = 34

    defaultConfig {
        applicationId = "ai.genie.node"
        minSdk = 26
        targetSdk = 34
        versionCode = 1
        versionName = "0.1.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
    // org.json is part of the platform, so the node has no third-party dependency at all —
    // the protocol is hand-rolled precisely so the node stays dependency-free and auditable.
    implementation("androidx.core:core-ktx:1.13.1")
}
