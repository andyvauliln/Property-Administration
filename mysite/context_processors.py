def current_user_context(request):
    user_role = request.user.role if request.user.is_authenticated else None
    return {'current_user': request.user, 'user_role': user_role}
