plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

// Cada compilación de Actions sube el número: Android solo acepta encima una
// versión con número mayor. En una compilación local queda en 1.
val numero = (System.getenv("GITHUB_RUN_NUMBER") ?: "1").toInt()
// La llave la entrega el workflow desde los secretos del repo. Sin ella se
// firma con la de depuración, que cambia en cada máquina: sirve para probar,
// no para actualizar encima de una instalación anterior.
val llave: String? = System.getenv("NOWCAST_KEYSTORE")

android {
    namespace = "io.github.vavodeleon.nowcast"
    compileSdk = 35

    defaultConfig {
        applicationId = "io.github.vavodeleon.nowcast"
        minSdk = 26
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

    buildFeatures { compose = true }

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
    implementation("androidx.glance:glance-appwidget:1.1.1")
    implementation("androidx.work:work-runtime-ktx:2.9.1")
    implementation(platform("androidx.compose:compose-bom:2024.10.01"))
    implementation("androidx.compose.ui:ui-graphics")
    implementation("androidx.compose.ui:ui-unit")
}
