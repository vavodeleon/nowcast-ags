plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

val numero = (System.getenv("GITHUB_RUN_NUMBER") ?: "1").toInt()
val llave: String? = System.getenv("NOWCAST_KEYSTORE")

android {
    namespace = "io.github.vavodeleon.nowcast.wear"
    compileSdk = 35

    defaultConfig {
        // El mismo id que el celular: así Wear OS los reconoce como pareja.
        applicationId = "io.github.vavodeleon.nowcast"
        minSdk = 30
        targetSdk = 34
        versionCode = numero
        versionName = "1.$numero"
    }

    signingConfigs {
        if (llave != null) {
            create("propia") {
                storeFile = file(llave)
                storePassword = System.getenv("NOWCAST_KEYSTORE_PASS")
                keyAlias = System.getenv("NOWCAST_KEY_ALIAS")
                keyPassword = System.getenv("NOWCAST_KEY_PASS")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.getByName(if (llave != null) "propia" else "debug")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    lint {
        checkReleaseBuilds = false
        abortOnError = false
    }
}

kotlin {
    compilerOptions { jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17) }
}

dependencies {
    implementation(project(":common"))
    implementation("androidx.wear.tiles:tiles:1.4.1")
    implementation("androidx.wear.protolayout:protolayout:1.2.1")
    implementation("androidx.concurrent:concurrent-futures-ktx:1.2.0")
    implementation("com.google.guava:guava:33.3.1-android")
}
