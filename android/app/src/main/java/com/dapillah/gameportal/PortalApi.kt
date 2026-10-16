package com.dapillah.gameportal

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import retrofit2.Retrofit
import retrofit2.converter.kotlinx.serialization.asConverterFactory
import retrofit2.http.*
import java.util.concurrent.TimeUnit

interface PortalApi {
    @POST("users") suspend fun createUser(@Body body: NewUser): User
    @GET("users/{id}") suspend fun user(@Path("id") id: String): User
    @GET("games") suspend fun games(): List<Game>
    @GET("games/{id}/versions/{version}") suspend fun game(@Path("id") id: String, @Path("version") version: Int): Game
    @GET("matches") suspend fun mine(): List<Match>
    @GET("matches/open") suspend fun open(): List<Match>
    @GET("matches/{id}") suspend fun match(@Path("id") id: String): Match
    @POST("matches") suspend fun create(@Body body: NewMatch): Match
    @POST("matches/{id}/join") suspend fun join(@Path("id") id: String): Match
    @POST("matches/{id}/start") suspend fun start(@Path("id") id: String, @Body body: StartMatch = StartMatch()): Match
    @POST("matches/{id}/leave") suspend fun leave(@Path("id") id: String): Match
    @PATCH("matches/{id}") suspend fun computers(@Path("id") id: String, @Body body: Computers): Match
    @DELETE("matches/{id}") suspend fun delete(@Path("id") id: String)
    @POST("matches/{id}/moves") suspend fun move(@Path("id") id: String, @Body body: Move): Match
    @POST("agent/chat") suspend fun agentChat(@Body body: AgentChatRequest): AgentChatResponse
    @POST("games") suspend fun createGame(@Body body: NewGame): Game
}

fun makeApi(baseUrl: String, credentials: () -> Credentials?): PortalApi {
    val client = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS).readTimeout(120, TimeUnit.SECONDS)
        .callTimeout(130, TimeUnit.SECONDS).retryOnConnectionFailure(false)
        .followRedirects(false).followSslRedirects(false)
        .addInterceptor { chain ->
            val request = chain.request().newBuilder()
            credentials()?.let { request.header("X-User-Id", it.id).header("X-User-Password", it.password) }
            chain.proceed(request.build())
        }.build()
    return Retrofit.Builder().baseUrl(baseUrl).client(client)
        .addConverterFactory(PortalJson.asConverterFactory("application/json".toMediaType()))
        .build().create(PortalApi::class.java)
}
