plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.simpletechsa.datanotes"
    compileSdk = 35
    defaultConfig {
        applicationId = "com.simpletechsa.datanotes"
        minSdk = 24
        targetSdk = 35
        versionCode = (System.getenv("VERSION_CODE") ?: "1").toInt()
        versionName = System.getenv("VERSION_NAME") ?: "1.0"
    }
    signingConfigs {
        create("release") {
            storeFile = file("../keystore/release.jks")
            storePassword = System.getenv("KS_PASS") ?: "datanotes"
            keyAlias = "datanotes"
            keyPassword = System.getenv("KS_PASS") ?: "datanotes"
        }
    }
    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.getByName("release")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
}

dependencies {
    implementation("androidx.webkit:webkit:1.12.1")
}
