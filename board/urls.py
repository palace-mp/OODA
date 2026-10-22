from django.urls import path

from . import views

app_name = "board"

urlpatterns = [
    path("", views.index, name="index"),
    path("add/", views.create, name="create"),
    path("new/", views.new, name="new"),
    path("inbox/", views.inbox, name="inbox"),
    path("inbox/write/", views.add_entry, name="inbox_entry"),
    path("inbox/<int:pk>/file/", views.file_entry, name="file_entry"),
    path("tempo/", views.tempo, name="tempo"),
    path("<int:number>/", views.item, name="item"),
    path("<int:number>/edit/", views.edit, name="edit"),
    path("<int:number>/phase/", views.phase, name="phase"),
    path("<int:number>/move/", views.move, name="move"),
    path("<int:number>/done/", views.done, name="done"),
    path("<int:number>/doc/<int:doc_pk>/", views.item, name="document"),
    path("<int:number>/save/", views.save_document, name="save"),
    path("<int:number>/review/", views.submit_review, name="review"),
    path("<int:number>/link/", views.add_link, name="link"),
    path("<int:number>/wait/", views.depend, name="depend"),
    path("<int:number>/wait/<int:other>/remove/", views.undepend, name="undepend"),
    path("<int:number>/merge/", views.merge, name="merge"),
    path("<int:number>/comment/", views.comment, name="comment"),
    path("entry/<int:pk>/delete/", views.delete_entry, name="delete_entry"),
    path("link/<int:pk>/delete/", views.delete_link, name="delete_link"),
    path("comment/<int:pk>/reply/", views.reply, name="reply"),
    path("comment/<int:pk>/status/", views.comment_status, name="comment_status"),
]
